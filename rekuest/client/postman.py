"""A GraphQL postman"""

from types import TracebackType
from typing import Any
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from rath.scalars import ID
from rekuest.client.graphql import RekuestGraphQL
from arkitekt_spec.declare.task import HookInput
from arkitekt_runtime.types import (
    ResolvedDependencyInput,
    TaskEventKind,
    AssignInput,
)
from rekuest.api.schema import Task, TaskChange, TaskChangeEvent, TaskEventChange
from rekuest.scalars import ActionHash
import asyncio
import uuid
from pydantic import Field, PrivateAttr
import logging
from arkitekt_runtime.postmans.errors import PostmanException, RootOnlyAssignError
from rekuest.client.rath import RekuestRath
from koil.composition import KoiledModel

logger = logging.getLogger(__name__)


class _FeedLost:
    """Queue sentinel: the task change feed died, so no event will ever arrive again."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


class GraphQLPostman(KoiledModel):
    """A GraphQL Postman

    This postman is used to send messages to the GraphQL server via a graphql
    transport.

    This graphql postman

    """

    rath: RekuestRath
    connected: bool = Field(default=False)
    tasks: dict[str, TaskChange] = Field(default_factory=dict)
    cancel_timeout: float = Field(
        default=5.0,
        description="Maximum seconds to wait for the server to confirm cancellation of a task when an assign stream is cancelled. Bounds cancellation so cancelling a call can never hang.",
    )
    catch_up_interval: float = Field(
        default=0.5,
        description="Seconds until a call reads its task's events again while the change feed has delivered nothing yet (so is not known to be subscribed). Doubles with every read, up to ten times this.",
    )

    _ass_update_queues: dict[str, asyncio.Queue[TaskEventChange | _FeedLost]] = PrivateAttr(
        default_factory=lambda: {}
    )
    # The change feed (TaskEventChange) only carries the task id, not the
    # client-generated reference the queues are keyed by, so we bind them here.
    # An event can arrive before that binding is known (websocket faster than the
    # assign http response, or an event before its create), so unbound events are
    # buffered and flushed in `_bind`.
    _task_to_reference: dict[str, str] = PrivateAttr(default_factory=lambda: {})
    _reference_to_task: dict[str, str] = PrivateAttr(default_factory=lambda: {})
    _orphan_events_by_task: dict[str, list[TaskEventChange]] = PrivateAttr(
        default_factory=lambda: {}
    )
    _watch_tasks_task: asyncio.Task[None] | None = None

    _watching: bool = PrivateAttr(default=False)
    _lock: asyncio.Lock | None = None
    # Subscribing to the change feed is not acknowledged, so the feed is only known
    # to be subscribed once it has delivered something. What a task reported before
    # that never arrives on it: until then a call reads its task's events instead
    # (`_acatch_up`), and whatever came both ways is handed on once (`_route`).
    _feed_live: bool = PrivateAttr(default=False)
    _routed_event_ids: dict[str, set[str]] = PrivateAttr(default_factory=lambda: {})
    _catch_up_tasks: dict[str, asyncio.Task[None]] = PrivateAttr(default_factory=lambda: {})

    def _bind(self, task_id: str, reference: str) -> None:
        """Bind a durable task id to its client-generated reference.

        Records both directions and flushes any events that arrived for this task
        before the binding was known into the reference's queue.
        """
        self._task_to_reference[task_id] = reference
        self._reference_to_task[reference] = task_id
        orphans = self._orphan_events_by_task.pop(task_id, [])
        queue = self._ass_update_queues.get(reference)
        if queue is not None:
            for event in orphans:
                queue.put_nowait(event)

    def _route(self, event: TaskEventChange) -> None:
        """Hand an event to the call waiting on its task, once.

        An event can come both from the feed and from a catch-up read. One whose task
        is not bound to a live reference yet is buffered, and flushed by `_bind`.
        """
        routed = self._routed_event_ids.setdefault(event.task, set())
        if event.id in routed:
            return
        routed.add(event.id)
        reference = self._task_to_reference.get(event.task)
        queue = self._ass_update_queues.get(reference) if reference is not None else None
        if queue is not None:
            queue.put_nowait(event)
        else:
            self._orphan_events_by_task.setdefault(event.task, []).append(event)

    async def _aread_events(self, task_id: str) -> list[TaskEventChange]:
        """The events a task has so far, oldest first, as the feed would carry them."""
        task = await RekuestGraphQL(self.rath).atask_events(id=task_id)
        return [
            TaskEventChange(
                id=event.id,
                task=task.id,
                kind=event.kind,
                returns=event.returns,
                message=event.message,
                progress=event.progress,
                value=event.value,
                createdAt=event.created_at,
            )
            for event in task.events
        ]

    async def _acatch_up(self, task_id: str) -> None:
        """Hand on what a task reported and the feed did not deliver.

        The task's own history decides the order: whatever is still waiting on the
        call's queue is put back behind the events it missed, so a call never sees a
        later event before an earlier one. Events the call already took are left out,
        and ones newer than the history that was read stay at the end.
        """
        history = await self._aread_events(task_id)
        reference = self._task_to_reference.get(task_id)
        queue = self._ass_update_queues.get(reference) if reference is not None else None
        if queue is None:
            return  # the call ended while its history was read

        waiting: list[TaskEventChange | _FeedLost] = []
        while not queue.empty():
            waiting.append(queue.get_nowait())
        waiting_ids = {event.id for event in waiting if isinstance(event, TaskEventChange)}

        routed = self._routed_event_ids.setdefault(task_id, set())
        taken = routed - waiting_ids
        read = {event.id for event in history}
        for event in history:
            if event.id not in taken:
                routed.add(event.id)
                queue.put_nowait(event)
        for item in waiting:
            if isinstance(item, _FeedLost) or item.id not in read:
                queue.put_nowait(item)

    async def _acatch_up_safely(self, task_id: str) -> None:
        """Catch up, and leave the call to the feed alone if that fails."""
        try:
            await self._acatch_up(task_id)
        except Exception:
            logger.warning("Could not read the events of task %s", task_id, exc_info=True)

    async def _acatch_up_until_live(self, task_id: str) -> None:
        """Keep reading a task's events for as long as the feed has shown nothing.

        A task that reported everything before the feed was subscribed never shows
        up on it, so its call would wait forever. Backs off: a task that is merely
        quiet is not asked twice a second for as long as it runs.
        """
        delay = self.catch_up_interval
        while not self._feed_live:
            await asyncio.sleep(delay)
            if self._feed_live:
                return  # `watch_tasks` caught every call up when the feed came alive
            await self._acatch_up_safely(task_id)
            delay = min(delay * 2, self.catch_up_interval * 10)

    @staticmethod
    def _reject_non_root(
        parent: ID | None, dependency: str | None, method: str | None
    ) -> None:
        """Refuse call shapes a GraphQL assign cannot express.

        Raises:
            RootOnlyAssignError: If the call carries a ``parent``, ``dependency`` or
                ``method`` — none of which a GraphQL assign can express.
        """
        if parent is not None or dependency is not None or method is not None:
            raise RootOnlyAssignError(
                "A GraphQL assign creates a root task, so it cannot carry "
                f"parent={parent!r}, dependency={dependency!r}, method={method!r}. "
                "A call made from inside a running task has to be originated over the "
                "agent socket, which happens automatically while an actor body runs "
                "(the agent's caller postman is bound as the current postman). Seeing "
                "this means the call is running outside a task, or a GraphQL postman "
                "was passed explicitly."
            )

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
        dependencies: Sequence[ResolvedDependencyInput] | None = None,
        step: bool | None = None,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
        call_key: str | None = None,
    ) -> AsyncGenerator[TaskEventChange, None]:
        """Originate a root task over GraphQL and stream its events.

        See :meth:`rekuest.postmans.types.Postman.aassign`. ``parent`` /
        ``dependency`` / ``method`` are accepted only so this postman can reject them
        loudly: the mutation creates a root task and has no way to express a child.
        ``call_key`` names a child by its parent, so a root has none; it is accepted and
        unused.
        """
        self._reject_non_root(parent, dependency, method)
        assign_input = AssignInput(
            action=action,
            resolution=resolution,
            implementation=implementation,
            agent=agent,
            action_hash=action_hash,
            interface=interface,
            hooks=tuple(hooks) if hooks is not None else None,
            args=args,
            reference=reference or str(uuid.uuid4()),
            capture=capture,
            dependencies=tuple(dependencies) if dependencies is not None else None,
            step=step,
        )
        # `reference` is always set above, so this is a str for the queue keys below.
        assign_reference: str = assign_input.reference or ""

        if not self._lock:
            raise ValueError("Postman was never connected")

        async with self._lock:
            if not self._watching:
                await self.start_watching()

        self._ass_update_queues[assign_reference] = asyncio.Queue()
        queue = self._ass_update_queues[assign_reference]

        # A task assigned while the feed is known to be subscribed misses nothing.
        feed_was_live = self._feed_live

        try:
            task = await self._send_assign(assign_input)
        except BaseException as e:
            # No task was bound to this reference, so nothing else will drop its queue.
            self._cleanup_reference(assign_reference)
            if isinstance(e, Exception):
                raise PostmanException(f"Cannot Assign: {e}") from e
            raise

        # Bind task id -> reference so the change feed (which only knows the task
        # id) can route events to this queue. Also flushes any events that raced
        # ahead of this http response.
        self._bind(task.id, assign_reference)

        if not feed_was_live:
            # Before the first event is handed on: this is what puts them in order.
            await self._acatch_up_safely(task.id)
            if not self._feed_live:
                self._catch_up_tasks[assign_reference] = asyncio.create_task(
                    self._acatch_up_until_live(task.id)
                )

        try:
            while True:
                signal = await queue.get()
                if isinstance(signal, _FeedLost):
                    # The change feed this call depends on is gone: no further event
                    # can ever arrive on this queue. Fail instead of waiting forever.
                    raise PostmanException(
                        f"Lost the task event feed while waiting for task {task.id}: {signal.reason}"
                    )
                yield signal
                queue.task_done()

        except asyncio.CancelledError as e:
            # Tell the server to cancel and await the CANCELLED confirmation (and,
            # if requested, escalate to an interrupt) before re-raising. The whole
            # exchange is bounded by `cancel_timeout`, so cancelling can never hang.
            try:
                await self._confirm_cancellation(
                    task.id,
                    queue,
                    escalate_to_interrupt,
                    cancel_timeout
                    if cancel_timeout is not None
                    else self.cancel_timeout,
                )
            finally:
                self._cleanup_reference(assign_reference)
            raise e
        finally:
            # Also the ordinary way out: the consumer stops iterating once it has seen
            # a terminal event, which closes this generator. Idempotent.
            self._cleanup_reference(assign_reference)

    def _cleanup_reference(self, reference: str) -> None:
        """Drop all per-call state for a finished/cancelled assignation."""
        tid = self._reference_to_task.pop(reference, None)
        if tid is not None:
            self._task_to_reference.pop(tid, None)
            self._orphan_events_by_task.pop(tid, None)
            self._routed_event_ids.pop(tid, None)
        self._ass_update_queues.pop(reference, None)
        catching_up = self._catch_up_tasks.pop(reference, None)
        if catching_up is not None:
            catching_up.cancel()

    async def _send_assign(self, assign_input: AssignInput) -> Task:
        """Create the task on the backend."""
        return await RekuestGraphQL(self.rath).aassign(**assign_input.model_dump())

    def _feed(self) -> AsyncIterator[TaskChangeEvent]:
        """The change feed of this client's tasks."""
        return RekuestGraphQL(self.rath).awatch_my_tasks()

    async def _confirm_cancellation(
        self,
        task_id: str,
        queue: "asyncio.Queue[TaskEventChange | _FeedLost]",
        escalate_to_interrupt: bool,
        timeout: float,
    ) -> None:
        """Cancel a task and await its CANCELLED (or escalated INTERRUPTED) event.

        Sends the cancel, then waits up to ``timeout`` for the backend to confirm
        via a CANCELLED task event. If that does not arrive and
        ``escalate_to_interrupt`` is set, sends a forceful interrupt and waits for
        the INTERRUPTED confirmation. Every wait is bounded, so this never hangs.
        """
        await self._send_cancel(task_id, timeout)
        if await self._await_kind(queue, {TaskEventKind.CANCELLED}, timeout):
            return

        if not escalate_to_interrupt:
            logger.warning(
                "Timed out awaiting CANCELLED confirmation for task %s", task_id
            )
            return

        await self._send_interrupt(task_id, timeout)
        if not await self._await_kind(
            queue, {TaskEventKind.INTERRUPTED, TaskEventKind.CANCELLED}, timeout
        ):
            logger.warning(
                "Timed out awaiting INTERRUPTED confirmation for task %s", task_id
            )

    async def _send_cancel(self, task_id: str, timeout: float) -> None:
        """Request a graceful cancel of the task (best-effort, bounded)."""
        try:
            await asyncio.wait_for(
                RekuestGraphQL(self.rath).acancel(task=task_id), timeout=timeout
            )
        except Exception:
            logger.warning(
                "Failed to request cancel for task %s", task_id, exc_info=True
            )

    async def _send_interrupt(self, task_id: str, timeout: float) -> None:
        """Request a forceful interrupt of the task (best-effort, bounded)."""
        try:
            await asyncio.wait_for(
                RekuestGraphQL(self.rath).ainterrupt(task=task_id), timeout=timeout
            )
        except Exception:
            logger.warning(
                "Failed to request interrupt for task %s", task_id, exc_info=True
            )

    async def _await_kind(
        self,
        queue: "asyncio.Queue[TaskEventChange | _FeedLost]",
        kinds: "set[TaskEventKind]",
        timeout: float,
    ) -> bool:
        """Drain ``queue`` until an event of one of ``kinds`` arrives.

        Bounded by ``timeout`` overall. Returns ``True`` if a matching event was
        seen, ``False`` on timeout.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                return False
            if isinstance(event, _FeedLost):
                return False  # no confirmation can arrive any more — stop waiting
            if event.kind in kinds:
                return True

    async def watch_tasks(self) -> None:
        """Watch this client's tasks and route their events to per-call queues.

        The change feed yields slim, non-traversable snapshots: ``create`` is a
        ``TaskChange`` (carrying both the task id and the reference) and ``event``
        is a ``TaskEventChange`` (carrying only the task id). We bind on ``create``
        and route on ``event``, buffering events whose task id is not yet bound.

        The first change is also the first proof that the feed is subscribed. Every
        call already waiting is caught up before that change is handed on: what its
        task reported until now comes from the task's history, in order, and
        everything after it from the feed.
        """
        try:
            async for change in self._feed():
                if not self._feed_live:
                    self._feed_live = True
                    for task_id in list(self._task_to_reference):
                        await self._acatch_up_safely(task_id)
                if change.create and change.create.reference:
                    self._bind(change.create.id, change.create.reference)
                if change.event:
                    self._route(change.event)

        except asyncio.CancelledError:
            raise  # ``stop_watching``: an orderly shutdown, nobody is left waiting
        except Exception as e:
            logger.error("Watching Tasks failed", exc_info=True)
            self._fail_pending(f"{type(e).__name__}: {e}")
            raise e
        else:
            # The subscription ended without an error (the server closed the stream).
            # Same consequence for everyone still waiting on it.
            self._fail_pending("the subscription ended")

    def _fail_pending(self, reason: str) -> None:
        """The change feed died: wake every call waiting on it, and allow a restart.

        Nothing consumes this task's result, so an exception here used to vanish —
        while every in-flight ``aassign`` kept awaiting a queue that could never be
        fed again. ``_watching`` is reset so the next call starts a fresh feed.
        """
        self._watching = False
        self._feed_live = False
        for queue in list(self._ass_update_queues.values()):
            queue.put_nowait(_FeedLost(reason))

    async def start_watching(self) -> None:
        """Start watching for updates"""
        logger.info("Starting watching")
        self._watch_tasks_task = asyncio.create_task(self.watch_tasks())
        self._watching = True

    async def stop_watching(self) -> None:
        """Causes the postman to stop watching"""
        if self._watch_tasks_task:
            self._watch_tasks_task.cancel()

            try:
                await asyncio.gather(
                    self._watch_tasks_task,
                    return_exceptions=True,
                )
            except asyncio.CancelledError:
                pass

        self._watching = False
        self._feed_live = False

    async def __aenter__(self) -> "GraphQLPostman":
        """Enter the postman"""
        self._lock = asyncio.Lock()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the context manager"""
        if self._watching:
            await self.stop_watching()
        return await super().__aexit__(exc_type, exc_val, exc_tb)
