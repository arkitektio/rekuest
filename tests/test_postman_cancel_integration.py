"""Integration smoke test for postman cancel-confirmation against the real backend.

Stands up a fresh ``Rekuest`` (via :func:`build_fresh_rekuest`) on the shared
deployment, registers a long-running action, starts a call, then cancels it. Because
the GraphQL postman now awaits the backend's CANCELLED confirmation before re-raising,
by the time the ``CancelledError`` surfaces the server-side task must already be in the
CANCELLED state — which we assert by reading it back over GraphQL.
"""

import asyncio
import uuid

import pytest
from dokker import Deployment

from arkitekt_runtime.types import TaskEventKind

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest


async def _find_task(client, reference: str):  # noqa: ANN001
    """Return the task with ``reference`` from the backend, or ``None``."""
    for task in await client.arequests():
        if task.reference == reference:
            return task
    return None


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_cancel_awaits_backend_cancelled(deployment: Deployment) -> None:
    """Cancelling a call reaches the backend and the task ends up CANCELLED."""

    app = build_fresh_rekuest(deployment, token="standalone_token")

    async def sleeper(seconds: int) -> int:
        """Sleep for a while, then return — long enough to be cancelled mid-flight."""
        await asyncio.sleep(seconds)
        return seconds

    app.register(sleeper)

    async with app as app:
        await app.aconnect(timeout=CONNECT_TIMEOUT)
        loop_task = asyncio.create_task(app.aloop())

        impl = await app.amy_implementation_at("sleeper")

        reference = f"cancel-smoke-{uuid.uuid4().hex[:8]}"
        call_task = asyncio.create_task(
            app.acall(impl, seconds=30, reference=reference)
        )

        # Wait until the backend has registered the task and recorded an event for it.
        #
        # `latestEventKind` is NOT a readiness signal: the backend leaves it at QUEUED
        # for the whole run and only moves it on the terminal event, so a task that is
        # already executing still reads as QUEUED. The event log is what we can observe
        # moving, so we gate on that — it says the task exists on the backend, which is
        # all the cancel below needs.
        async def _started() -> bool:
            task = await _find_task(app, reference)
            return task is not None and len(task.events) > 0

        deadline = asyncio.get_event_loop().time() + 10.0
        while not await _started():
            assert asyncio.get_event_loop().time() < deadline, "task never started"
            await asyncio.sleep(0.2)

        # Cancel: the postman sends the cancel and awaits the CANCELLED confirmation
        # before this CancelledError propagates.
        call_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call_task

        # The confirmation the postman awaits is bounded: on a slow runner it can give
        # up first, and the task's row then catches up a moment later.
        deadline = asyncio.get_event_loop().time() + 10.0
        while True:
            task = await _find_task(app, reference)
            assert task is not None, "task disappeared from the backend"
            if task.latest_event_kind == TaskEventKind.CANCELLED:
                break
            assert asyncio.get_event_loop().time() < deadline, (
                f"expected CANCELLED, got {task.latest_event_kind}"
            )
            await asyncio.sleep(0.2)

        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("step", ["_send_assign", "_acatch_up_safely"])
async def test_a_call_cancelled_before_its_events_stream_cancels_its_task(
    deployment: Deployment, step: str
) -> None:
    """The server holds the task before the call reads its events: a cancel then still reaches it.

    ``step`` is held up after it is done -- the assign's answer on its way back, or the
    first read of the task's events -- so the cancel lands exactly there.
    """
    app = build_fresh_rekuest(deployment, token="standalone_token")

    async def sleeper(seconds: int) -> int:
        """Sleep for a while, then return — long enough to be cancelled mid-flight."""
        await asyncio.sleep(seconds)
        return seconds

    app.register(sleeper)

    async with app as app:
        await app.aconnect(timeout=CONNECT_TIMEOUT)
        loop_task = asyncio.create_task(app.aloop())
        impl = await app.amy_implementation_at("sleeper")

        done = getattr(app.postman, step)

        async def held_up(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            result = await done(*args, **kwargs)
            await asyncio.sleep(3)
            return result

        object.__setattr__(app.postman, step, held_up)

        reference = f"cancel-early-{uuid.uuid4().hex[:8]}"
        call_task = asyncio.create_task(
            app.acall(impl, seconds=30, reference=reference)
        )

        deadline = asyncio.get_event_loop().time() + 10.0
        while (task := await _find_task(app, reference)) is None or not task.events:
            assert asyncio.get_event_loop().time() < deadline, (
                "task never reached the backend"
            )
            await asyncio.sleep(0.05)

        call_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call_task

        task = await _find_task(app, reference)
        assert task is not None
        assert task.latest_event_kind == TaskEventKind.CANCELLED

        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
