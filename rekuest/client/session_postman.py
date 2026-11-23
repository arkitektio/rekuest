"""The postman of a caller: calls over a caller session on takt (``/session``).

A call is an ``ASSIGN`` on the session, and its task's events are the session's
``task:<id>`` topic: numbered in commit order and resumed after a dropped connection, so
nothing is missed and nothing comes twice. The GraphQL postman had to read a task's history
whenever its feed might have dropped something; this one never has to.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator, Sequence
from types import TracebackType
from typing import Any

from arkitekt_runtime.postmans.errors import (
    STATEFUL_NEEDS_AGENT,
    PostmanException,
    RootOnlyAssignError,
    StatefulNeedsAgent,
)
from arkitekt_runtime.types import (
    AssignInput,
    ResolvedDependencyInput,
    StateRequirementInput,
    TaskEventKind,
    TaskPolicy,
)
from arkitekt_spec.declare.task import AssignPrompt, HookInput, StateRequirement
from arkitekt_spec.scalars import ID
from koil.composition import KoiledModel
from pydantic import ConfigDict, Field

from rekuest.api.schema import TaskEventChange
from rekuest.client.session import (
    CallerSession,
    Data,
    Refused,
    Resync,
    SessionClosed,
    SessionRefusal,
)
from rekuest.scalars import ActionHash

logger = logging.getLogger(__name__)

#: How long a call waits for the answer to a question about its task.
INQUIRY_TIMEOUT = 10.0


def event_of(item: dict[str, Any]) -> TaskEventChange:
    """A task event item of the session, as the change feeds carried it."""
    return TaskEventChange.model_validate(
        {
            "id": item["id"],
            "task": item["task"],
            "kind": item["kind"],
            "returns": item.get("returns"),
            "message": item.get("message"),
            "progress": item.get("progress"),
            "value": item.get("value"),
            "createdAt": item["created_at"],
        }
    )


class SessionPostman(KoiledModel):
    """Originates root tasks over a caller session and streams their events.

    Entering it opens the session; it reconnects and resumes by itself while entered.
    """

    session: CallerSession = Field(exclude=True)
    cancel_timeout: float = Field(
        default=5.0,
        description="Maximum seconds to wait for the server to confirm cancellation of a task when an assign stream is cancelled. Bounds cancellation so cancelling a call can never hang.",
    )
    inquire_after: float = Field(
        default=30.0,
        description="Seconds of silence from a task after which its call asks the task's agent whether it is still on it, and again after every further such silence. An agent that no longer knows the task has lost it, and the call ends with AgentLost. A call's own inquire_after overrides this; 0 never asks.",
    )
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def connected(self) -> bool:
        """Whether a call can go out right now (one made while not waits for the socket)."""
        return self.session.connected

    async def __aenter__(self) -> SessionPostman:
        """Open the session."""
        await self.session.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the session."""
        await self.session.aclose()
        return await super().__aexit__(exc_type, exc_val, exc_tb)

    @staticmethod
    def _reject_non_root(
        parent: ID | None, dependency: str | None, method: str | None
    ) -> None:
        """Refuse call shapes a root assign cannot express.

        Raises:
            RootOnlyAssignError: If the call carries a ``parent``, ``dependency`` or
                ``method``.
        """
        if parent is not None or dependency is not None or method is not None:
            raise RootOnlyAssignError(
                "A caller session assigns root tasks, so it cannot carry "
                f"parent={parent!r}, dependency={dependency!r}, method={method!r}. "
                "A call made from inside a running task is originated over the agent "
                "socket, which happens automatically while an actor body runs (the "
                "agent's caller postman is bound as the current postman). Seeing this "
                "means the call is running outside a task, or this postman was passed "
                "explicitly."
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
        prompt: AssignPrompt | None = None,
        policy: TaskPolicy | None = None,
        state_requires: list[StateRequirement] | None = None,
        on_lost: str | None = None,
        max_resumes: int | None = None,
        inquire_after: float | None = None,
        deadman: int | None = None,
    ) -> AsyncGenerator[TaskEventChange, None]:
        """Originate a root task over the session and stream its events, in order.

        See :meth:`rekuest.postmans.types.Postman.aassign`. ``call_key`` and ``prompt``
        belong to children and are unused for a root. ``deadman``: cancel the task once
        this client has been gone that many seconds (the dead man switch).
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
            policy=policy,
            on_lost=on_lost,
            max_resumes=max_resumes,
            state_requires=(
                tuple(
                    StateRequirementInput.model_validate(requirement.model_dump())
                    for requirement in state_requires
                )
                if state_requires
                else None
            ),
        )
        timeout = cancel_timeout if cancel_timeout is not None else self.cancel_timeout
        frame: dict[str, Any] = {
            "type": "ASSIGN",
            "input": assign_input.model_dump(mode="json", exclude_none=True),
        }
        if deadman is not None:
            frame["deadman"] = deadman

        # Shielded: the server holds the task before its answer is back, so a call
        # cancelled in between still learns the task's id, and cancels it.
        assigning = asyncio.ensure_future(self.session.call(frame))
        try:
            answer = await asyncio.shield(assigning)
        except asyncio.CancelledError:
            task_id = await self._assigned_despite_cancel(assigning, timeout)
            if task_id is not None:
                async with self.session.watching(f"task:{task_id}") as feed:
                    await self._confirm_cancellation(
                        task_id, feed, escalate_to_interrupt, timeout
                    )
            raise
        except SessionRefusal as e:
            if str(e).startswith(STATEFUL_NEEDS_AGENT):
                raise StatefulNeedsAgent(str(e)) from e
            raise PostmanException(f"Cannot Assign: {e}") from e
        except SessionClosed as e:
            raise PostmanException(f"Cannot Assign: {e}") from e

        task_id = str(answer["task"])
        handed: set[str] = set()
        ask_after = self.inquire_after if inquire_after is None else inquire_after
        async with self.session.watching(f"task:{task_id}") as feed:
            try:
                loop = asyncio.get_running_loop()
                heard = loop.time()
                while True:
                    wait = None
                    if ask_after > 0:
                        wait = max(heard + ask_after - loop.time(), 0.05)
                    try:
                        signal = await asyncio.wait_for(feed.get(), timeout=wait)
                    except TimeoutError:
                        # Silent for long enough to wonder: is its agent still on it? An
                        # agent that is not has lost it; the server ends the task, and that
                        # end arrives on the feed.
                        await self._aask_about(task_id)
                        heard = loop.time()
                        continue
                    heard = loop.time()
                    if isinstance(signal, Refused):
                        raise PostmanException(
                            f"Cannot follow task {task_id}: {signal.error}"
                        )
                    items = (
                        signal.snapshot.get("events", [])
                        if isinstance(signal, Resync)
                        else [signal.item]
                    )
                    for item in items:
                        event = event_of(item)
                        if event.id in handed:
                            continue
                        handed.add(event.id)
                        yield event
            except asyncio.CancelledError:
                # Tell the server to cancel and await the CANCELLED confirmation (and, if
                # requested, escalate to an interrupt) before re-raising. Bounded by
                # `cancel_timeout`, so cancelling can never hang.
                await self._confirm_cancellation(
                    task_id, feed, escalate_to_interrupt, timeout
                )
                raise

    async def _assigned_despite_cancel(
        self, assigning: asyncio.Future[dict[str, Any]], timeout: float
    ) -> str | None:
        """The task an assign in flight made, for a call cancelled before its answer."""
        try:
            answer = await asyncio.wait_for(asyncio.shield(assigning), timeout=timeout)
        except TimeoutError:
            assigning.cancel()
            logger.warning(
                "Cancelled before the assign answered; no task to cancel after %ss",
                timeout,
            )
            return None
        except Exception:  # noqa: BLE001 - a failed assign made no task to cancel
            return None
        return str(answer["task"])

    async def _aask_about(self, task_id: str) -> None:
        """Ask the task's agent whether it is still on it. Bounded; asking is best effort."""
        try:
            answer = await asyncio.wait_for(
                self.session.call({"type": "INQUIRE", "task": task_id}),
                timeout=INQUIRY_TIMEOUT,
            )
        except Exception:  # noqa: BLE001 - asking is best effort; the call goes on waiting
            logger.debug("Asking about task %s failed", task_id, exc_info=True)
            return
        logger.debug("Task %s, asked about after a silence: %s", task_id, answer)

    async def _control(self, kind: str, task_id: str, timeout: float) -> None:
        """Send a control (best effort, bounded)."""
        try:
            await asyncio.wait_for(
                self.session.call({"type": kind, "task": task_id}), timeout=timeout
            )
        except Exception:  # noqa: BLE001 - logged; the caller is already leaving
            logger.warning("Failed to %s task %s", kind.lower(), task_id, exc_info=True)

    async def _confirm_cancellation(
        self,
        task_id: str,
        feed: asyncio.Queue[Resync | Data | Refused],
        escalate_to_interrupt: bool,
        timeout: float,
    ) -> None:
        """Cancel a task and await its CANCELLED (or escalated INTERRUPTED) event."""
        await self._control("CANCEL", task_id, timeout)
        if await self._await_kind(feed, {TaskEventKind.CANCELLED}, timeout):
            return
        if not escalate_to_interrupt:
            logger.warning(
                "Timed out awaiting CANCELLED confirmation for task %s", task_id
            )
            return
        await self._control("INTERRUPT", task_id, timeout)
        if not await self._await_kind(
            feed, {TaskEventKind.INTERRUPTED, TaskEventKind.CANCELLED}, timeout
        ):
            logger.warning(
                "Timed out awaiting INTERRUPTED confirmation for task %s", task_id
            )

    @staticmethod
    async def _await_kind(
        feed: asyncio.Queue[Resync | Data | Refused],
        kinds: set[TaskEventKind],
        timeout: float,
    ) -> bool:
        """Drain ``feed`` until an event of one of ``kinds`` arrives; bounded by ``timeout``."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while (remaining := deadline - loop.time()) > 0:
            try:
                signal = await asyncio.wait_for(feed.get(), timeout=remaining)
            except TimeoutError:
                return False
            if isinstance(signal, Refused):
                return False
            items = (
                signal.snapshot.get("events", [])
                if isinstance(signal, Resync)
                else [signal.item]
            )
            if any(TaskEventKind(item["kind"]) in kinds for item in items):
                return True
        return False
