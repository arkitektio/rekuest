"""The caller session and its postman against a scripted stand-in for takt's ``/session``.

Backend-free: a websocket server in the test speaks the session frames (takt's
``tests/fixtures/session_wire_examples.json`` shapes), so the client's own parts are under
test: hello and resume, in-order and once-only events, calls answered once, cancellation.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio
import websockets
from arkitekt_runtime.postmans.errors import PostmanException
from arkitekt_runtime.types import TaskEventKind
from websockets.asyncio.server import ServerConnection

from rekuest.client.session import CallerSession
from rekuest.client.session_postman import SessionPostman

LIMITS = {"session_grace": 60, "ping_interval": 1, "replay_max": 500, "deadman_min": 15}


def event(seq: int, kind: str = "LOG", task: str = "7", **extra: Any) -> dict[str, Any]:
    """A task event item as takt sends it."""
    return {
        "id": f"ev{seq}",
        "task": task,
        "kind": kind,
        "message": extra.get("message"),
        "progress": None,
        "returns": extra.get("returns"),
        "level": None,
        "value": None,
        "created_at": "2026-11-23T10:00:00Z",
        "task_seq": seq,
    }


class FakeTakt:
    """A session server that answers HELLO, records frames, and lets a test push frames."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.hellos: list[dict[str, Any]] = []
        self.connections: list[ServerConnection] = []
        self.pos = 0
        self.on_frame: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self.server: Any = None
        self.welcome_delay = 0.0

    @property
    def url(self) -> str:
        port = self.server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}/session"

    async def push(self, frame: dict[str, Any]) -> None:
        if "pos" in frame and frame["pos"] is None:
            self.pos += 1
            frame = {**frame, "pos": self.pos}
        await self.connections[-1].send(json.dumps(frame))

    async def handler(self, connection: ServerConnection) -> None:
        self.connections.append(connection)
        async for raw in connection:
            frame = json.loads(raw)
            self.frames.append(frame)
            if frame["type"] == "HELLO":
                self.hellos.append(frame)
                await asyncio.sleep(self.welcome_delay)
                resumed = frame.get("session") == "s-1"
                await connection.send(
                    json.dumps(
                        {
                            "type": "WELCOME",
                            "session": "s-1",
                            "resumed": resumed,
                            "limits": LIMITS,
                        }
                    )
                )
            elif self.on_frame is not None:
                await self.on_frame(frame)

    def sent(self, kind: str) -> list[dict[str, Any]]:
        return [frame for frame in self.frames if frame["type"] == kind]


@pytest_asyncio.fixture
async def takt() -> AsyncIterator[FakeTakt]:
    fake = FakeTakt()
    fake.server = await websockets.serve(fake.handler, "127.0.0.1", 0)
    try:
        yield fake
    finally:
        fake.server.close()
        await fake.server.wait_closed()


async def token() -> str:
    return "token"


async def until(predicate: Callable[[], object], timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_call_streams_its_task_in_order_and_once(takt: FakeTakt) -> None:
    async def script(frame: dict[str, Any]) -> None:
        if frame["type"] == "ASSIGN":
            assert frame["input"]["action"] == "12"
            assert frame["input"]["args"] == {"myPort": 1}
            await takt.push(
                {
                    "type": "ASSIGNED",
                    "pos": None,
                    "request": frame["request"],
                    "reference": "r",
                    "task": "7",
                    "created": True,
                    "record": None,
                }
            )
        elif frame["type"] == "WATCH":
            assert frame["topic"] == "task:7"
            # What happened between the assign and the watch: in the snapshot.
            await takt.push(
                {
                    "type": "RESYNC",
                    "pos": None,
                    "topic": "task:7",
                    "reason": "new",
                    "snapshot": {
                        "task": None,
                        "events": [event(1, "QUEUED")],
                        "task_seq": 1,
                    },
                }
            )
            await takt.push(
                {
                    "type": "DATA",
                    "pos": None,
                    "topic": "task:7",
                    "item": event(1, "QUEUED"),
                }
            )
            await takt.push(
                {
                    "type": "DATA",
                    "pos": None,
                    "topic": "task:7",
                    "item": event(2, "YIELD", returns={"r": 1}),
                }
            )
            await takt.push(
                {
                    "type": "DATA",
                    "pos": None,
                    "topic": "task:7",
                    "item": event(2, "YIELD", returns={"r": 1}),
                }
            )
            await takt.push(
                {
                    "type": "DATA",
                    "pos": None,
                    "topic": "task:7",
                    "item": event(3, "COMPLETED"),
                }
            )

    takt.on_frame = script
    async with SessionPostman(session=CallerSession(takt.url, token)) as postman:
        kinds = []
        stream = postman.aassign(args={"myPort": 1}, action="12", inquire_after=0)
        async with contextlib.aclosing(stream):
            async for change in stream:
                kinds.append(change.kind)
                if change.kind == TaskEventKind.COMPLETED:
                    break
        # A stream that ends stops following its task.
        await until(lambda: takt.sent("UNWATCH"))
        assert takt.sent("UNWATCH")[0]["topic"] == "task:7"
    assert kinds == [TaskEventKind.QUEUED, TaskEventKind.YIELD, TaskEventKind.COMPLETED]
    assert takt.hellos[0]["token"] == "token"


@pytest.mark.asyncio
async def test_a_dropped_socket_resumes_the_session_and_resends_an_unanswered_call(
    takt: FakeTakt,
) -> None:
    calls: list[str] = []

    async def script(frame: dict[str, Any]) -> None:
        if frame["type"] == "INQUIRE":
            calls.append(frame["request"])
            if len(calls) == 1:
                # The answer is lost with the socket.
                await takt.connections[-1].close()
            else:
                await takt.push(
                    {
                        "type": "ACCEPTED",
                        "pos": None,
                        "request": frame["request"],
                        "result": {"task": "7"},
                    }
                )

    takt.on_frame = script
    session = CallerSession(takt.url, token)
    async with session:
        await session.wait_open(timeout=3)
        await takt.push({"type": "DATA", "pos": None, "topic": "x", "item": {}})
        await until(lambda: session.last_pos == 1)
        answer = await asyncio.wait_for(
            session.call({"type": "INQUIRE", "task": "7"}), timeout=10
        )
    assert answer["result"] == {"task": "7"}
    # The same request, sent again on the resumed session.
    assert len(calls) == 2 and calls[0] == calls[1]
    assert takt.hellos[-1]["session"] == "s-1"
    assert takt.hellos[-1]["last_pos"] == 1


@pytest.mark.asyncio
async def test_a_refused_assign_says_why(takt: FakeTakt) -> None:
    async def script(frame: dict[str, Any]) -> None:
        if frame["type"] == "ASSIGN":
            await takt.push(
                {
                    "type": "REFUSED",
                    "pos": None,
                    "request": frame["request"],
                    "error": "No such action.",
                }
            )

    takt.on_frame = script
    async with SessionPostman(session=CallerSession(takt.url, token)) as postman:
        with pytest.raises(PostmanException, match="No such action"):
            async for _ in postman.aassign(args={}, action="404"):
                pass


@pytest.mark.asyncio
async def test_cancelling_a_call_cancels_its_task_and_waits_for_the_confirmation(
    takt: FakeTakt,
) -> None:
    async def script(frame: dict[str, Any]) -> None:
        if frame["type"] == "ASSIGN":
            await takt.push(
                {
                    "type": "ASSIGNED",
                    "pos": None,
                    "request": frame["request"],
                    "reference": "r",
                    "task": "7",
                    "created": True,
                    "record": None,
                }
            )
        elif frame["type"] == "WATCH":
            await takt.push(
                {
                    "type": "RESYNC",
                    "pos": None,
                    "topic": "task:7",
                    "reason": "new",
                    "snapshot": {
                        "task": None,
                        "events": [event(1, "STARTED")],
                        "task_seq": 1,
                    },
                }
            )
        elif frame["type"] == "CANCEL":
            await takt.push(
                {
                    "type": "ACCEPTED",
                    "pos": None,
                    "request": frame["request"],
                    "result": {"task": "7"},
                }
            )
            await takt.push(
                {
                    "type": "DATA",
                    "pos": None,
                    "topic": "task:7",
                    "item": event(2, "CANCELLED"),
                }
            )

    takt.on_frame = script
    async with SessionPostman(session=CallerSession(takt.url, token)) as postman:
        started = asyncio.Event()

        async def consume() -> None:
            async for change in postman.aassign(args={}, action="12", inquire_after=0):
                if change.kind == TaskEventKind.STARTED:
                    started.set()

        running = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=3)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    assert takt.sent("CANCEL")[0]["task"] == "7"


@pytest.mark.asyncio
async def test_nothing_but_hello_goes_out_before_the_welcome(takt: FakeTakt) -> None:
    takt.welcome_delay = 0.3
    session = CallerSession(takt.url, token)
    async with session:
        await until(lambda: takt.hellos)
        async with session.watching("task:9"):
            await session.wait_open(timeout=3)
            await until(lambda: takt.sent("WATCH"))
    kinds = [frame["type"] for frame in takt.frames]
    assert kinds[0] == "HELLO"
    assert kinds.index("WATCH") > 0


@pytest.mark.asyncio
async def test_a_session_that_cannot_open_fails_its_calls_after_max_backoff() -> None:
    """A refused socket is retried, but a call does not wait for it forever."""
    from rekuest.client.session import CallerSession, SessionUnavailable

    async def token() -> str:
        return "token"

    # Nothing listens on port 9: every connect is refused at once.
    async with CallerSession(
        "ws://127.0.0.1:9/session", token, max_backoff=0.5
    ) as session:
        with pytest.raises(SessionUnavailable, match="could not be opened"):
            await asyncio.wait_for(session.call({"type": "ASSIGN"}), timeout=10)
