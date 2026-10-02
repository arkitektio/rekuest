"""A call gets every event of its task, in order, however late the feed subscribes.

Subscribing to the change feed is not acknowledged: the postman starts it right
before its first assign and cannot tell when the backend has it. Whatever the task
reports until then is never delivered on the feed. So until the feed has delivered
something, a call reads its task's events instead, and the two are merged: nothing
twice, nothing out of order.

Backend-free, with hand-written doubles: a postman whose three seams to the backend
(`_send_assign`, `_feed`, `_aread_events`) are a task id, a queue the test feeds, and
a list the test appends to.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from pydantic import PrivateAttr

from arkitekt_runtime.types import AssignInput, TaskEventKind
from rath.links.testing.direct_succeeding_link import DirectSucceedingLink
from rekuest.api.schema import Task, TaskChangeEvent, TaskEventChange
from rekuest.client.postman import GraphQLPostman
from rekuest.client.rath import RekuestRath

TERMINAL = {TaskEventKind.COMPLETED, TaskEventKind.FAILED, TaskEventKind.CRITICAL}


def _event(
    number: int, kind: TaskEventKind = TaskEventKind.YIELD, task: str = "t1"
) -> TaskEventChange:
    return TaskEventChange(
        id=f"e{number}", task=task, kind=kind, createdAt=datetime.now(UTC)
    )


class Backend(GraphQLPostman):
    """A postman whose backend is in the test's hands."""

    _history: dict[str, list[TaskEventChange]] = PrivateAttr(default_factory=lambda: {})
    _feed_queue: "asyncio.Queue[TaskChangeEvent]" = PrivateAttr(
        default_factory=asyncio.Queue
    )
    _assigned: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
    _answer_assign: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
    _reads: list[str] = PrivateAttr(default_factory=lambda: [])
    _reads_fail: bool = PrivateAttr(default=False)
    _task_ids: list[str] = PrivateAttr(default_factory=lambda: ["t1", "t2"])

    async def _send_assign(self, assign_input: AssignInput) -> Task:
        task_id = self._task_ids.pop(0)
        self._assigned.set()
        await self._answer_assign.wait()
        return Task.model_construct(id=task_id)

    async def _feed(self) -> AsyncIterator[TaskChangeEvent]:
        while True:
            yield await self._feed_queue.get()

    async def _aread_events(self, task_id: str) -> list[TaskEventChange]:
        self._reads.append(task_id)
        if self._reads_fail:
            raise RuntimeError("the backend does not answer")
        return list(self._history.get(task_id, []))

    def reports(self, event: TaskEventChange, *, on_feed: bool) -> None:
        """The task reports an event: it is in its history, and on the feed if subscribed."""
        self._history.setdefault(event.task, []).append(event)
        if on_feed:
            self._feed_queue.put_nowait(TaskChangeEvent(event=event))


def _backend(*, answers_assign: bool = True) -> Backend:
    backend = Backend(
        rath=RekuestRath(link=DirectSucceedingLink()), catch_up_interval=0.01
    )
    if answers_assign:
        backend._answer_assign.set()
    return backend


async def _call(backend: Backend) -> list[str]:
    """Run one call to its end; the ids of the events it was handed, in order."""
    seen: list[str] = []
    async for event in backend.aassign(args={}, action="a1"):
        seen.append(event.id)
        if event.kind in TERMINAL:
            break
    return seen


@pytest.mark.asyncio
async def test_what_a_task_reported_before_the_feed_subscribed_is_read() -> None:
    backend = _backend()
    async with backend:
        backend.reports(_event(1), on_feed=False)
        backend.reports(_event(2), on_feed=False)
        call = asyncio.create_task(_call(backend))
        await backend._assigned.wait()

        backend.reports(_event(3), on_feed=True)
        backend.reports(_event(4, TaskEventKind.COMPLETED), on_feed=True)

        assert await asyncio.wait_for(call, timeout=1) == ["e1", "e2", "e3", "e4"]


@pytest.mark.asyncio
async def test_a_task_that_ended_before_the_feed_subscribed_still_ends_its_call() -> (
    None
):
    """Nothing of it ever arrives on the feed: the call keeps reading until it has it."""
    backend = _backend()
    async with backend:
        call = asyncio.create_task(_call(backend))
        await backend._assigned.wait()
        await asyncio.sleep(0)  # the call binds and reads a history that is still empty

        backend.reports(_event(1), on_feed=False)
        backend.reports(_event(2, TaskEventKind.COMPLETED), on_feed=False)

        assert await asyncio.wait_for(call, timeout=1) == ["e1", "e2"]


@pytest.mark.asyncio
async def test_an_event_the_feed_delivered_first_stays_behind_the_ones_it_missed() -> (
    None
):
    """The feed comes alive while the assign is still unanswered, with a later event."""
    backend = _backend(answers_assign=False)
    async with backend:
        call = asyncio.create_task(_call(backend))
        await backend._assigned.wait()

        backend.reports(_event(1), on_feed=False)
        backend.reports(_event(2), on_feed=False)
        backend.reports(_event(3), on_feed=True)
        while not backend._feed_queue.empty():
            await asyncio.sleep(0)
        backend._answer_assign.set()

        backend.reports(_event(4, TaskEventKind.COMPLETED), on_feed=True)

        assert await asyncio.wait_for(call, timeout=1) == ["e1", "e2", "e3", "e4"]


@pytest.mark.asyncio
async def test_a_call_made_once_the_feed_is_subscribed_reads_nothing() -> None:
    backend = _backend()
    async with backend:
        first = asyncio.create_task(_call(backend))
        await backend._assigned.wait()
        backend.reports(_event(1, TaskEventKind.COMPLETED), on_feed=True)
        await asyncio.wait_for(first, timeout=1)
        backend._reads.clear()

        second = asyncio.create_task(_call(backend))
        await asyncio.sleep(0)
        backend.reports(_event(2, task="t2"), on_feed=True)
        backend.reports(_event(3, TaskEventKind.COMPLETED, task="t2"), on_feed=True)

        assert await asyncio.wait_for(second, timeout=1) == ["e2", "e3"]
        assert backend._reads == []


@pytest.mark.asyncio
async def test_a_history_that_cannot_be_read_leaves_the_call_to_the_feed() -> None:
    backend = _backend()
    backend._reads_fail = True
    async with backend:
        call = asyncio.create_task(_call(backend))
        await backend._assigned.wait()

        backend.reports(_event(1), on_feed=True)
        backend.reports(_event(2, TaskEventKind.COMPLETED), on_feed=True)

        assert await asyncio.wait_for(call, timeout=1) == ["e1", "e2"]
        assert backend._reads, "the history was never asked for"
