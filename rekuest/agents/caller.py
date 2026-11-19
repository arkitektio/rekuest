"""The agent-as-caller postman.

When actor code calls another action or a dependency *from inside a running task*,
the call should travel over the agent's own WebSocket as an ``AssignRequest`` instead of
going out through the GraphQL postman. ``AgentPostman`` is the object that makes that work:
it satisfies the :class:`~rekuest.postmans.types.Postman` protocol (``aassign`` →
``AsyncGenerator`` of task events), so every existing call path in
:mod:`rekuest.invoke` (``_acall`` / ``_aiterate``) and :mod:`rekuest.calls`
(``acall_dependency``) routes through it unchanged: a :class:`~rekuest.task.Task` reads it
off the agent running it, and the dependency proxies pass it as ``postman=``. Nothing picks
it out of the ambient context.

The translation is:

- outbound: the call description (:meth:`Postman.aassign`'s arguments) → an
  ``AssignRequest`` socket message; ``reference`` is the caller's own idempotency key. A
  child call (one with a ``parent``) takes its parent's next task step and sends it as
  ``parent_step``: the server makes ``ASSIGN_REQUEST`` idempotent on ``(parent, parent_step)``
  too, and stores the step as the child's ``parent_step``.
- inbound: the backend answers with an ``AssignResponse`` (carrying the durable task id) and
  then streams ``ExecutionEvent`` mirrors for that task. Each surfaced mirror is adapted into a
  :class:`CallerTaskEvent` that exposes exactly the ``.kind`` / ``.returns`` / ``.message``
  attributes ``rekuest.calls._astream_raw`` reads.

The agent's message loop (``BaseAgent.process``) forwards ``AssignResponse`` /
``ControlResponse`` / ``ExecutionEvent`` here via the ``handle_*`` methods.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from types import TracebackType
from typing import Any
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence

from rath.scalars import ID

from arkitekt_runtime import messages
from arkitekt_runtime.trace import current_traceparent
from arkitekt_runtime.types import TaskEventKind, TaskPolicy
from arkitekt_spec.declare.task import AssignPrompt, HookInput, StateRequirement
from arkitekt_runtime.agents.transport.types import MessageSink
from arkitekt_runtime.postmans.errors import STATEFUL_NEEDS_AGENT, AssignException, StatefulNeedsAgent
from arkitekt_spec.declare.errors import NonDeterministicWorkflow
from rekuest.scalars import ActionHash

logger = logging.getLogger(__name__)


@dataclass
class CallerTaskEvent:
    """A minimal ``TaskEvent`` look-alike.

    ``rekuest.calls._astream_raw`` only ever reads ``.kind`` (compared against
    :class:`TaskEventKind`), ``.returns`` and ``.message`` — so this three-field adapter is
    enough to drive the existing call machinery without constructing a full (frozen,
    field-heavy) GraphQL ``TaskEvent``.
    """

    kind: TaskEventKind
    returns: dict[str, object] | None = None
    message: str | None = None
    value: dict[str, object] | None = None
    """LOST: what is known (started, last_progress, effects, reason). FAILED: the
    report's ``code``, when it has one (``STATE_PRECONDITION``)."""
    task: str | None = None
    """The task the event is about (DENIED: the refused child)."""


#: Mirror message types that end a delegated task's stream. Every other mirror
#: (Bound/Queued/Started/Progress/Log/Delegate/…ing/…ed) is consumed for
#: bookkeeping only — ``_astream_raw`` would ignore it anyway. Defined alongside the
#: reports it mirrors in :mod:`rekuest.messages`.
_TERMINAL_TYPES = messages.TERMINAL_EVENT_MIRRORS


def _adapt(event: "messages.ExecutionEvent") -> CallerTaskEvent | None:
    """Translate a backend mirror into a ``CallerTaskEvent`` (or ``None`` to skip).

    Only the kinds ``_astream_raw`` acts on are surfaced, and HELD, so a caller can see
    that its child waits for approval. ``Cancelled``/``Interrupted``
    are surfaced as a ``CRITICAL`` so an in-flight ``acall`` fails loudly rather than hanging
    or silently completing when the work it delegated was killed out from under it.
    """
    if isinstance(event, messages.YieldEvent):
        return CallerTaskEvent(kind=TaskEventKind.YIELD, returns=event.returns)
    if isinstance(event, messages.CompletedEvent):
        return CallerTaskEvent(kind=TaskEventKind.COMPLETED)
    if isinstance(event, messages.FailedEvent):
        return CallerTaskEvent(
            kind=TaskEventKind.FAILED,
            message=event.error,
            value={"code": event.code} if event.code else None,
            task=event.task,
        )
    if isinstance(event, messages.CriticalEvent):
        return CallerTaskEvent(kind=TaskEventKind.CRITICAL, message=event.error)
    if isinstance(event, messages.LostEvent):
        return CallerTaskEvent(
            kind=TaskEventKind.LOST,
            message=event.reason,
            value={
                "started": event.started,
                "last_progress": event.last_progress,
                "effects": event.effects,
                "reason": event.reason,
            },
        )
    if isinstance(event, messages.HeldEvent):
        return CallerTaskEvent(
            kind=TaskEventKind.HELD,
            message=event.prompt.title if event.prompt is not None else None,
            task=event.task,
        )
    if isinstance(event, messages.DeniedEvent):
        return CallerTaskEvent(kind=TaskEventKind.DENIED, message=event.answer, task=event.task)
    if isinstance(event, (messages.CancelledEvent, messages.InterruptedEvent)):
        return CallerTaskEvent(
            kind=TaskEventKind.CRITICAL,
            message="The delegated task was cancelled or interrupted before completion.",
        )
    return None


#: How long a call waits for the answer to a question about its task.
INQUIRY_TIMEOUT = 10.0


class _Lost:
    """Put on a call's queue when its task cannot be followed any further."""

    def __init__(self, error: str) -> None:
        self.error = error


def _response_id(
    response: "messages.AssignResponse | messages.ProbeResponse",
) -> str | None:
    """The id the backend assigned, whichever kind of answer this is.

    An assign is answered with a durable ``task`` id; a probe with an ephemeral ``probe``
    id (``p-…``). Everything downstream — the event queues, cancellation, cleanup — keys
    off this one value, which is why the two flows can share a single path.
    """
    if isinstance(response, messages.ProbeResponse):
        return response.probe
    return response.task


class AgentPostman:
    """A :class:`Postman` that originates work over the agent's socket.

    A single instance is shared by every actor on the agent; all per-call state is keyed by
    request id / task id, so concurrent calls never collide. It depends only on a
    :class:`~rekuest.agents.transport.types.MessageSink` — somewhere to put a
    message — rather than on the agent, so it cannot reach past the socket it was given.
    """

    def __init__(
        self,
        sink: MessageSink,
        cancel_timeout: float = 5.0,
        child_step: "Callable[[str], Awaitable[int | None]] | None" = None,
        inquire_after: float = 30.0,
    ) -> None:
        self.sink = sink
        # Seconds of silence from a child after which its call asks whether its agent is
        # still on it (a call's own ``inquire_after`` overrides this; 0 never asks).
        self.inquire_after = inquire_after
        # Takes the parent task's next step for a child call: its parent_step.
        self.child_step = child_step
        # Max seconds to await a CANCELLED/INTERRUPTED confirmation when an assign
        # stream is cancelled. Bounds cancellation so it can never hang.
        self.cancel_timeout = cancel_timeout
        # request id -> future resolved with the AssignResponse or ProbeResponse
        self._pending_responses: dict[
            str,
            "asyncio.Future[messages.AssignResponse | messages.ProbeResponse]",
        ] = {}
        # durable task id -> queue of ExecutionEvent mirrors
        self._task_queues: dict[str, "asyncio.Queue[Any]"] = {}
        # task id -> mirrors that arrived before the AssignResponse was processed
        self._orphan_by_task: dict[str, list["messages.ExecutionEvent"]] = {}
        # (parent task, call) -> how often the parent has made that call: the derived
        # call key's occurrence
        self._occurrences: dict[tuple[str, str], int] = {}
        # request ids of calls given up before their answer came: the child the answer
        # names is cancelled, as nobody waits for it
        self._abandoned: set[str] = set()
        self._cancelling: set["asyncio.Future[None]"] = set()
        # request id of a probe under way -> the probe id its response named (None until
        # then): what is failed when the connection drops (see fail_probes)
        self._probes: dict[str, str | None] = {}
        # request ids of questions under way (a state's revision, an inquiry): unnumbered,
        # so an answer lost with its connection never comes (see fail_questions)
        self._questions: set[str] = set()

    @property
    def connected(self) -> bool:
        """Whether work can currently be originated over the socket."""
        return self.sink.connected

    # ------------------------------------------------------------------ outbound

    async def aassign(  # noqa: PLR0913 - the call description, mirrored from the protocol
        self,
        *,
        args: dict[str, Any],
        capture: bool = False,
        reference: str | None = None,
        hooks: Sequence[HookInput] | None = None,
        action: ID | None = None,
        implementation: ID | None = None,
        parent: ID | None = None,
        dependency: str | None = None,
        method: str | None = None,
        action_hash: ActionHash | None = None,
        agent: ID | None = None,
        interface: str | None = None,
        resolution: ID | None = None,
        step: bool | None = None,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
        call_key: str | None = None,
        prompt: "AssignPrompt | None" = None,
        policy: "TaskPolicy | None" = None,
        state_requires: "list[StateRequirement] | None" = None,
        inquire_after: float | None = None,
        on_lost: "messages.OnLost | None" = None,
        max_resumes: int | None = None,
    ) -> AsyncGenerator[CallerTaskEvent, None]:
        """Originate a task over the agent socket and stream its events.

        See :meth:`rekuest.postmans.types.Postman.aassign`. Unlike the GraphQL
        mutation, the socket carries the whole task tree, so ``parent`` / ``dependency``
        / ``method`` are sent as given — that is the reason actor-internal calls route
        here at all.

        Sends an ``AssignRequest``, awaits the ``AssignResponse`` (to learn the durable task
        id), then yields a ``CallerTaskEvent`` for every surfaced mirror until a terminal one
        arrives. On cancellation a ``CancelRequest`` is sent and the ``CancelledEvent``
        confirmation is awaited (bounded by ``cancel_timeout``); if ``escalate_to_interrupt``
        is set and the cancel is not confirmed in time, an ``InterruptRequest`` follows.

        ``policy`` is accepted and unused: a child takes its parent's, and the server
        never lets an agent choose one for the work it asks for.

        ``on_lost`` says what becomes of the child should its agent be lost while it runs
        (:class:`~arkitekt_runtime.messages.OnLost`). Left out, a workflow resumes from
        what it recorded and anything else ends lost; ``FAIL`` opts a workflow out of that.

        ``agent`` goes with ``interface`` (that agent's implementation of it) or with
        ``action`` / ``action_hash`` (this action, on that agent). The second is part of
        the call key derived for it: the same action with the same args on two agents is
        two different children, which a resuming workflow must not find swapped.
        """
        parent_step = (
            await self.child_step(str(parent))
            if parent is not None and self.child_step is not None
            else None
        )
        if parent is not None and call_key is None:
            target = action or action_hash or implementation or (f"{dependency}.{method}" if dependency else interface) or "call"
            if agent is not None and (action or action_hash):
                target = f"{target}@{agent}"
            call_key = self._derive_call_key(str(parent), target, args)
        # The reference stays the caller's own; the parent's step travels next to it.
        request_reference = reference or str(uuid.uuid4())
        request = messages.AssignRequest(
            reference=request_reference,
            parent_step=parent_step,
            call_key=call_key,
            args=dict(args or {}),
            action=action,
            action_hash=action_hash,
            implementation=implementation,
            agent=agent,
            interface=interface,
            parent=parent,
            dependency=dependency,
            method=method,
            resolution=resolution,
            hooks=[h.model_dump(by_alias=True) for h in (hooks or [])],
            capture=capture,
            step=step,
            prompt=prompt,
            state_requires=list(state_requires or ()),
            on_lost=on_lost,
            max_resumes=max_resumes,
            # Where in the running trace the call is made: the child continues from here.
            traceparent=current_traceparent(),
        )
        async for event in self._astream(
            request,
            request_reference,
            escalate_to_interrupt,
            cancel_timeout,
            self.inquire_after if inquire_after is None else inquire_after,
        ):
            yield event

    async def aprobe(
        self,
        *,
        args: dict[str, Any],
        reference: str | None = None,
        action: ID | None = None,
        implementation: ID | None = None,
        action_hash: ActionHash | None = None,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
    ) -> AsyncGenerator[CallerTaskEvent, None]:
        """Fire a probe over the agent socket and stream its events.

        A probe is an ephemeral, zero-persistence invocation under this agent's own
        identity: no server-side history, no replay or recovery, and no task tree — probes
        are always provenance roots. Only actions declaring ``allowProbe`` accept one.

        It therefore takes a narrower call description than :meth:`aassign`: no parent, no
        dependency resolution and no hooks, because none of them can apply.

        The event stream is shaped exactly like :meth:`aassign`'s; the id it is keyed by is
        the probe id (``p-…``) rather than a durable task id. Resends are *not* idempotent:
        there is no durable row to dedupe against, so a resend after a lost response fires a
        new probe.
        """
        request_reference = reference or str(uuid.uuid4())
        request = messages.ProbeRequest(
            reference=request_reference,
            args=dict(args or {}),
            action=action,
            action_hash=action_hash,
            implementation=implementation,
        )
        async for event in self._astream(
            request,
            request_reference,
            escalate_to_interrupt,
            cancel_timeout,
        ):
            yield event

    def fail_probes(self) -> None:
        """The connection was made anew: fail every probe still under way.

        A call to another action survives a dropped connection: its request is numbered
        and kept until acknowledged, and its answer and its child's events are commands
        the backend keeps until this agent acknowledged them. A probe is none of that. It
        is ephemeral, the backend ends it when the connection drops, and firing it again
        would run it twice: it fails here rather than waiting for an end that was lost.
        """
        lost = "The connection dropped while the probe ran; a probe is not resumed."
        for request_id, probe in list(self._probes.items()):
            future = self._pending_responses.get(request_id)
            if future is not None and not future.done():
                future.set_exception(AssignException(lost))
            elif probe is not None and probe in self._task_queues:
                self._task_queues[probe].put_nowait(_Lost(lost))

    def fail_questions(self) -> None:
        """The connection was made anew: fail every question still waiting for its answer.

        A question (a state's revision, an inquiry) has no position: it is not kept, and
        neither is its answer. One lost with the connection never arrives, and whoever
        asked would wait for ever. It fails here; asking again is the asker's to decide.
        """
        lost = AssignException("The connection dropped before the backend answered.")
        for request_id in list(self._questions):
            future = self._pending_responses.get(request_id)
            if future is not None and not future.done():
                future.set_exception(lost)

    def forget_parent(self, parent: str) -> None:
        """A parent task ended: its call counters are no longer needed."""
        for key in [k for k in self._occurrences if k[0] == parent]:
            del self._occurrences[key]

    def _derive_call_key(self, parent: str, target: str, args: dict[str, Any] | None) -> str:
        """What a parent calls a child it gave no key: the target, its args, the occurrence.

        The same on every run of a deterministic parent, whatever order concurrent calls
        take their steps in: that is what finds the child again when a workflow resumes.
        Two calls with the same target and args are told apart by occurrence, and are
        interchangeable anyway.
        """
        digest = hashlib.sha256(json.dumps(args or {}, sort_keys=True, default=str).encode()).hexdigest()[:8]
        base = f"{target}:{digest}"
        occurrence = self._occurrences.get((parent, base), 0) + 1
        self._occurrences[(parent, base)] = occurrence
        return f"{base}:{occurrence}"

    async def _astream(
        self,
        request: "messages.AssignRequest | messages.ProbeRequest",
        reference: str,
        escalate_to_interrupt: bool,
        cancel_timeout: float | None,
        inquire_after: float = 0.0,
    ) -> AsyncGenerator[CallerTaskEvent, None]:
        """Send one origination request and stream the resulting events until terminal.

        With ``inquire_after``, a task that has been silent that long is asked about
        (:meth:`ainquire`), and again after every further such silence. Asking changes
        nothing but this: an agent that no longer knows the task has lost it, the backend
        ends the task as lost, and that end is what this stream then yields.

        Shared by assigns and probes: the two differ only in which request goes out and
        which field of the answer carries the id, so the correlation, cancellation and
        cleanup below are identical for both.
        """
        loop = asyncio.get_running_loop()
        response_future: "asyncio.Future[messages.AssignResponse | messages.ProbeResponse]" = loop.create_future()
        self._pending_responses[request.id] = response_future

        task: str | None = None
        queue: "asyncio.Queue[messages.ExecutionEvent | _Lost] | None" = None
        ended = False
        if isinstance(request, messages.ProbeRequest):
            self._probes[request.id] = None
        try:
            await self.sink.asend(request)
            response = await response_future

            if response.error:
                if response.error.startswith("Nondeterministic workflow"):
                    raise NonDeterministicWorkflow(response.error)
                if response.error.startswith(STATEFUL_NEEDS_AGENT):
                    raise StatefulNeedsAgent(response.error)
                raise AssignException(response.error)
            task = _response_id(response)
            if not task:
                raise AssignException(
                    "The backend acked the request without an id and without an error."
                )

            queue = self._register_task(reference, task)
            if request.id in self._probes:
                self._probes[request.id] = task

            # A child found again (created=False) gets its events so far replayed, and live
            # ones may overlap with them: the same event arrives a second time.
            seen: set[str] = set()
            while True:
                if inquire_after > 0:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=inquire_after)
                    except TimeoutError:
                        await self._aask_about(task)
                        continue
                else:
                    event = await queue.get()
                if isinstance(event, _Lost):
                    raise AssignException(event.error)
                if event.event in seen:
                    continue
                seen.add(event.event)
                ended = isinstance(event, _TERMINAL_TYPES)
                adapted = _adapt(event)
                if adapted is not None:
                    yield adapted
                if ended:
                    return
        except asyncio.CancelledError:
            # Tell the backend to wind the delegated work down and await its CANCELLED
            # confirmation (escalating to an interrupt if requested) before re-raising.
            # Bounded by the cancel timeout, so it can never hang the caller being torn down.
            if task is not None and queue is not None:
                await self._confirm_cancellation(
                    task,
                    queue,
                    escalate_to_interrupt,
                    cancel_timeout
                    if cancel_timeout is not None
                    else self.cancel_timeout,
                )
            elif task is None and isinstance(request, messages.AssignRequest):
                # Given up before the answer came. The request is kept until the backend
                # has it, so the child will be made: it is cancelled when its answer says
                # which task it is.
                self._abandoned.add(request.id)
            raise
        except GeneratorExit:
            # Generator finalization (aclose): best-effort send only — awaiting event
            # delivery while the async generator is being torn down is fragile.
            # A stream closed at its last event has nothing left to cancel.
            if task is not None and not ended:
                await self._send_control(messages.CancelRequest(task=task), task)
            raise
        finally:
            self._pending_responses.pop(request.id, None)
            self._probes.pop(request.id, None)
            if task is not None:
                self._task_queues.pop(task, None)
                self._orphan_by_task.pop(task, None)

    def _register_task(
        self, reference: str, task: str
    ) -> "asyncio.Queue[Any]":
        """The queue of the task a response named, with any event that came before it."""
        return self._ensure_queue(task)

    def _ensure_queue(self, task: str) -> "asyncio.Queue[Any]":
        """Return ``task``'s event queue, creating it and draining any orphaned mirrors.

        Events can race ahead of the response that names their task; they are parked in
        ``_orphan_by_task`` until this creates the queue they belong in.
        """
        queue = self._task_queues.setdefault(task, asyncio.Queue())
        for event in self._orphan_by_task.pop(task, []):
            queue.put_nowait(event)
        return queue

    async def _confirm_cancellation(
        self,
        task: str,
        queue: "asyncio.Queue[Any]",
        escalate_to_interrupt: bool,
        timeout: float,
    ) -> None:
        """Cancel a delegated task and await its CANCELLED (or INTERRUPTED) mirror.

        Sends a ``CancelRequest``, then waits up to ``timeout`` for a
        ``CancelledEvent`` mirror. If that does not arrive and ``escalate_to_interrupt``
        is set, sends an ``InterruptRequest`` and waits for the ``InterruptedEvent``.
        Every wait is bounded, so this never hangs.
        """
        # Any end confirms it: a child that completed or failed as the cancel went out will
        # never say it was cancelled, and there is nothing to wait for.
        await self._send_control(messages.CancelRequest(task=task), task)
        if await self._await_terminal(queue, _TERMINAL_TYPES, timeout):
            return

        if not escalate_to_interrupt:
            logger.warning("Timed out awaiting CancelledEvent for task %s", task)
            return

        await self._send_control(messages.InterruptRequest(task=task), task)
        if not await self._await_terminal(queue, _TERMINAL_TYPES, timeout):
            logger.warning("Timed out awaiting InterruptedEvent for task %s", task)

    async def _send_control(
        self,
        request: "messages.CancelRequest | messages.InterruptRequest",
        task: str,
    ) -> None:
        """Send a lifecycle-control request over the socket (best-effort)."""
        try:
            await self.sink.asend(request)
        except Exception:
            logger.warning(
                "Failed to send %s for task %s",
                type(request).__name__,
                task,
                exc_info=True,
            )

    async def _await_terminal(
        self,
        queue: "asyncio.Queue[Any]",
        types: tuple[type["messages.ExecutionEvent"], ...],
        timeout: float,
    ) -> bool:
        """Drain ``queue`` until a mirror of one of ``types`` arrives.

        Bounded by ``timeout`` overall. Returns ``True`` if a matching mirror was
        seen, ``False`` on timeout.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                return False
            if isinstance(event, types):
                return True

    # ------------------------------------------------------------------- inbound

    def handle_response(
        self, message: "messages.AssignResponse | messages.ProbeResponse"
    ) -> None:
        """Resolve the waiting ``aassign``/``aprobe`` with the backend's response."""
        task = _response_id(message)
        future = self._pending_responses.get(message.request)
        if future is None or future.done():
            # Nobody waits for this answer: its call was given up, or was made by a process
            # before this one. A child nobody waits for is not left running.
            if message.request in self._abandoned:
                self._abandoned.discard(message.request)
                if task and not message.error:
                    self._cancel_unwanted(task)
            if task:
                self._orphan_by_task.pop(task, None)
            return
        if task and not message.error:
            # Pre-create the queue so events that raced ahead of this response are not
            # lost (the awaiting flow reuses the same queue via setdefault).
            self._ensure_queue(task)
        future.set_result(message)

    def _cancel_unwanted(self, task: str) -> None:
        """Cancel a child whose caller is gone, from where no one can await it."""
        cancelling = asyncio.ensure_future(self._send_control(messages.CancelRequest(task=task), task))
        self._cancelling.add(cancelling)
        cancelling.add_done_callback(self._cancelling.discard)

    def handle_assign_response(self, message: messages.AssignResponse) -> None:
        """Resolve the waiting ``aassign`` with its ``AssignResponse``."""
        self.handle_response(message)

    async def astate_revision(
        self,
        *,
        parent: str,
        dependency: str,
        state: str,
        since: dict[str, Any] | None = None,
        paths: Sequence[str] = (),
    ) -> "messages.StateRevisionResponse":
        """Ask the server about a state a workflow's guard watches (see ``task.guard``)."""
        request = messages.StateRevisionRequest(parent=parent, dependency=dependency, state=state, since=since, paths=list(paths))
        future: "asyncio.Future[Any]" = asyncio.get_running_loop().create_future()
        self._pending_responses[request.id] = future
        self._questions.add(request.id)
        try:
            await self.sink.asend(request)
            response = await future
        finally:
            self._pending_responses.pop(request.id, None)
            self._questions.discard(request.id)
        if response.error:
            raise AssignException(response.error)
        return response

    async def _aask_about(self, task: str) -> None:
        """Ask about a silent task, for what the asking does: the answer is the backend's
        to act on (a task its agent does not know ends lost, and the stream hears of it).
        Never in the way of the call: bounded, and a question that fails is only that."""
        try:
            answer = await asyncio.wait_for(self.ainquire(task), timeout=INQUIRY_TIMEOUT)
        except Exception:  # noqa: BLE001 — asking is best effort; the call goes on waiting
            logger.debug("Asking about task %s failed", task, exc_info=True)
            return
        logger.debug("Task %s, asked about after a silence: %s", task, answer)

    async def ainquire(self, task: str) -> "messages.InquireResponse":
        """Ask whether the agent of ``task``, which this agent assigned, is still on it.

        A read: nothing is done to the task, and nothing is tried again. The answer says
        what the task's agent knows of it (``status``), or why it was not asked
        (``detail``). An agent that no longer knows the task has lost it, and the backend
        ends the task as lost: the call waiting on it then ends with that. How long to wait
        before asking, and what to do with the answer, is the caller's.
        """
        request = messages.InquireRequest(task=task)
        future: "asyncio.Future[Any]" = asyncio.get_running_loop().create_future()
        self._pending_responses[request.id] = future
        self._questions.add(request.id)
        try:
            await self.sink.asend(request)
            response = await future
        finally:
            self._pending_responses.pop(request.id, None)
            self._questions.discard(request.id)
        if response.error:
            raise AssignException(response.error)
        return response

    def handle_inquire_response(self, message: messages.InquireResponse) -> None:
        """Resolve the waiting ``ainquire``."""
        future = self._pending_responses.get(message.request)
        if future is not None and not future.done():
            future.set_result(message)

    def handle_state_revision_response(self, message: messages.StateRevisionResponse) -> None:
        """Resolve the waiting ``astate_revision``."""
        future = self._pending_responses.get(message.request)
        if future is not None and not future.done():
            future.set_result(message)

    def handle_probe_response(self, message: messages.ProbeResponse) -> None:
        """Resolve the waiting ``aprobe`` with its ``ProbeResponse``."""
        self.handle_response(message)

    def handle_execution_event(self, message: messages.ExecutionEvent) -> None:
        """Route a task-event mirror to its task queue (buffering if the response is in flight)."""
        queue = self._task_queues.get(message.task)
        if queue is not None:
            queue.put_nowait(message)
        elif self._pending_responses:
            # An event can come before the answer that names its task: kept for a call
            # still waiting for one. With none waiting it belongs to nobody (a call of a
            # process before this one) and is dropped.
            self._orphan_by_task.setdefault(message.task, []).append(message)

    # --------------------------------------------------------- protocol niceties

    async def __aenter__(self) -> "AgentPostman":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None
