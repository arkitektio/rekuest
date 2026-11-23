"""A caller session on takt (``/session``): one socket over which a client calls actions and
follows tasks and states (takt's ``docs/session-protocol.md``).

The client keeps almost nothing: its session id, the newest ``pos`` it received (reported on
every ping), and the calls it still waits on. takt keeps what it watches and how far it got,
so a client back within the grace is sent exactly what it missed, and a call sent again after
a dropped connection is done once. What arrives twice at a resume's seam is dropped here, per
topic, before a watcher sees it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import ssl
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

#: Close codes of a session socket (takt ``session::frames::close``).
CLOSE_UNAUTHORIZED = 4401

#: The most item keys a topic remembers to drop a seam's duplicates.
SEEN_LIMIT = 4096


class SessionRefusal(Exception):
    """The server refused a call (or a watch), and said why."""


class SessionClosed(Exception):
    """The session was closed while a call or a watch waited on it."""


@dataclass
class Resync:
    """Replace what you hold for the topic with ``snapshot``.

    ``reason``: ``new`` (a first look), ``resumed`` (a list after a reconnect),
    ``too_far_behind``, ``agent_restarted``, ``pruned``, ``slow_consumer``,
    ``state_replaced``, or ``session_expired`` (the server had forgotten the session).
    """

    reason: str
    snapshot: Any


@dataclass
class Data:
    """One item of the topic, in order."""

    item: Any


@dataclass
class Refused:
    """The server will not follow the topic."""

    error: str


TopicEvent = Resync | Data | Refused


def _seen_key(topic: str, item: object) -> str | None:
    """A key for an item that may arrive twice at a seam; None when it carries none."""
    if not isinstance(item, dict):
        return None
    if topic.startswith("task:"):
        seq = item.get("task_seq")
        return f"e{seq}" if seq is not None else None
    if topic.startswith("tasks:"):
        event = item.get("event") or {}
        if event.get("task_seq") is not None:
            return f"e{event.get('task')}:{event['task_seq']}"
        create = item.get("create") or {}
        return f"c{create['id']}" if create.get("id") else None
    if topic.startswith(("children:", "agent:")):
        change = item.get("create") or item.get("update") or {}
        if not change.get("id"):
            return None
        return f"{'c' if item.get('create') else 'u'}{change['id']}:{change.get('revision')}"
    if topic.startswith("probe:"):
        seq = item.get("seq")
        return f"p{seq}" if seq is not None else None
    return None


@dataclass
class _Topic:
    queues: set[asyncio.Queue[TopicEvent]] = field(default_factory=set)
    seen: dict[str, None] = field(default_factory=dict)
    rev: int = 0
    expired: bool = False


@dataclass
class _Pending:
    frame: dict[str, Any]
    future: asyncio.Future[dict[str, Any]]


class CallerSession:
    """One caller session: connect it (``async with``), then :meth:`watching` and :meth:`call`.

    Args:
        endpoint_url: The session socket, ``ws(s)://<host>/<rekuest>/session``.
        token_loader: Loads the token to present; asked again on every (re)connect.
        ssl_context: For ``wss``.
        proxy: An HTTP forward proxy to tunnel the socket through (None: direct).
        client_info: What the server records of this client (app, version, ...).
    """

    def __init__(
        self,
        endpoint_url: str,
        token_loader: Callable[[], Awaitable[str]],
        *,
        ssl_context: ssl.SSLContext | None = None,
        proxy: str | None = None,
        client_info: dict[str, Any] | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.token_loader = token_loader
        self.ssl_context = ssl_context
        self.proxy = proxy
        self.client_info = client_info or {}
        self.session: str | None = None
        self.last_pos = 0
        self.ping_interval = 15.0
        self._socket: websockets.ClientConnection | None = None
        self._send_lock = asyncio.Lock()
        self._topics: dict[str, _Topic] = {}
        self._dropped: set[str] = set()
        self._pending: dict[str, _Pending] = {}
        self._open = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._closing = False

    # ------------------------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> CallerSession:
        self._closing = False
        self._runner = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the socket; every call and watch still waiting is told so."""
        self._closing = True
        if self._socket is not None:
            with contextlib.suppress(Exception):
                await self._socket.close()
        if self._runner is not None:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._runner
        self._runner = None
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(SessionClosed("The session was closed."))
        self._pending.clear()
        self._open.clear()

    @property
    def connected(self) -> bool:
        """Whether the session is open right now."""
        return self._open.is_set()

    async def wait_open(self, timeout: float | None = None) -> None:
        """Wait until the session is open (a WELCOME was received)."""
        await asyncio.wait_for(self._open.wait(), timeout=timeout)

    async def _run(self) -> None:
        """Connect, and connect again whenever the socket drops, until closed."""
        attempt = 0
        while not self._closing:
            try:
                token = await self.token_loader()
                async with websockets.connect(
                    self.endpoint_url,
                    ssl=self.ssl_context
                    if self.endpoint_url.startswith("wss")
                    else None,
                    proxy=self.proxy,
                ) as socket:
                    self._socket = socket
                    await self._send(
                        {
                            "type": "HELLO",
                            "token": token,
                            "session": self.session,
                            "last_pos": self.last_pos,
                            "client_info": self.client_info,
                        }
                    )
                    pinger = asyncio.create_task(self._ping())
                    try:
                        async for raw in socket:
                            attempt = 0
                            self._on_frame(json.loads(raw))
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as e:
                if e.rcvd is not None and e.rcvd.code == CLOSE_UNAUTHORIZED:
                    logger.warning(
                        "The session's token was refused; trying a fresh one"
                    )
            except Exception:  # noqa: BLE001 - the session reconnects whatever went wrong
                logger.debug("The session socket failed", exc_info=True)
            finally:
                self._socket = None
                self._open.clear()
            if self._closing:
                return
            wait = min(30.0, 1.0 * 2 ** min(attempt, 5)) + random.random()  # noqa: S311
            attempt += 1
            await asyncio.sleep(wait)

    async def _ping(self) -> None:
        """Liveness, and how far this client got (the server's resume record follows it)."""
        while True:
            await asyncio.sleep(self.ping_interval)
            await self._send({"type": "PING", "pos": self.last_pos})

    async def _send(self, frame: dict[str, Any]) -> bool:
        socket = self._socket
        if socket is None:
            return False
        try:
            async with self._send_lock:
                await socket.send(json.dumps(frame))
        except ConnectionClosed:
            return False
        return True

    def _send_soon(self, frame: dict[str, Any]) -> None:
        # Only once welcomed: takt closes a socket whose first frame is not HELLO, and
        # what was asked before is sent on WELCOME.
        if self._socket is not None and self._open.is_set():
            asyncio.ensure_future(self._send(frame))

    # --------------------------------------------------------------------------------- frames

    def _on_frame(self, frame: dict[str, Any]) -> None:
        pos = frame.get("pos")
        if isinstance(pos, int) and pos > self.last_pos:
            self.last_pos = pos
        kind = frame.get("type")
        if kind == "WELCOME":
            self._on_welcome(frame)
        elif kind == "DATA":
            self._on_data(frame["topic"], frame.get("item"))
        elif kind == "RESYNC":
            self._on_resync(
                frame["topic"], frame.get("snapshot"), frame.get("reason", "new")
            )
        elif kind in ("ASSIGNED", "ACCEPTED"):
            pending = self._pending.pop(frame.get("request", ""), None)
            if pending is not None and not pending.future.done():
                pending.future.set_result(frame)
        elif kind == "REFUSED":
            if frame.get("request"):
                pending = self._pending.pop(frame["request"], None)
                if pending is not None and not pending.future.done():
                    pending.future.set_exception(SessionRefusal(frame.get("error", "")))
            elif frame.get("topic"):
                logger.warning(
                    "The session would not follow %s: %s",
                    frame["topic"],
                    frame.get("error"),
                )
                for queue in self._topics.get(frame["topic"], _Topic()).queues:
                    queue.put_nowait(Refused(frame.get("error", "")))
            else:
                logger.warning("The session refused a frame: %s", frame.get("error"))
        elif kind == "CLOSING":
            logger.info("The session is closing: %s", frame.get("reason"))

    def _on_welcome(self, frame: dict[str, Any]) -> None:
        resumed = bool(frame.get("resumed"))
        if not resumed:
            self.last_pos = 0
            # A new session where there was one: the server watches nothing of ours any more,
            # and what it sends again is a catch-up, not a first look.
            if self.session is not None:
                for topic in self._topics.values():
                    topic.expired = True
        self.session = frame.get("session")
        limits = frame.get("limits") or {}
        self.ping_interval = float(max(1, int(limits.get("ping_interval", 15))))
        self._open.set()
        for name in list(self._dropped):
            self._send_soon({"type": "UNWATCH", "topic": name})
        self._dropped.clear()
        for name in self._topics:
            # One the server still watches ignores it; one it forgot starts again.
            self._send_soon({"type": "WATCH", "topic": name})
        for pending in self._pending.values():
            # Unanswered: sent again under the same id, done once.
            self._send_soon(pending.frame)

    def _on_data(self, name: str, item: object) -> None:
        topic = self._topics.get(name)
        if topic is None:
            return
        if name.startswith(("state:", "states:")):
            rev = item.get("global_rev") if isinstance(item, dict) else None
            if isinstance(rev, int):
                if rev <= topic.rev:
                    return
                topic.rev = rev
        else:
            key = _seen_key(name, item)
            if key is not None:
                if key in topic.seen:
                    return
                topic.seen[key] = None
                if len(topic.seen) > SEEN_LIMIT:
                    topic.seen.pop(next(iter(topic.seen)))
        for queue in topic.queues:
            queue.put_nowait(Data(item))

    def _on_resync(self, name: str, snapshot: object, reason: str) -> None:
        topic = self._topics.get(name)
        if topic is None:
            return
        if topic.expired and reason == "new":
            reason = "session_expired"
        topic.expired = False
        topic.seen.clear()
        topic.rev = snapshot.get("global_rev", 0) if isinstance(snapshot, dict) else 0
        events = snapshot.get("events") if isinstance(snapshot, dict) else None
        for event in events or []:
            if isinstance(event, dict) and event.get("task_seq") is not None:
                topic.seen[f"e{event['task_seq']}"] = None
        for queue in topic.queues:
            queue.put_nowait(Resync(reason, snapshot))

    # ----------------------------------------------------------------------------- the API

    @contextlib.asynccontextmanager
    async def watching(self, name: str) -> AsyncIterator[asyncio.Queue[TopicEvent]]:
        """Follow a topic while the block runs: its snapshot (a :class:`Resync`) then its
        items (:class:`Data`) arrive on the queue yielded. The first watcher of a topic makes
        the server watch it; the last one to leave stops it."""
        queue: asyncio.Queue[TopicEvent] = asyncio.Queue()
        topic = self._topics.get(name)
        if topic is None:
            topic = _Topic()
            self._topics[name] = topic
            self._dropped.discard(name)
            self._send_soon({"type": "WATCH", "topic": name})
        topic.queues.add(queue)
        try:
            yield queue
        finally:
            current = self._topics.get(name)
            if current is not None:
                current.queues.discard(queue)
                if not current.queues:
                    del self._topics[name]
                    if self._socket is not None and self._open.is_set():
                        self._send_soon({"type": "UNWATCH", "topic": name})
                    else:
                        self._dropped.add(name)

    async def call(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Ask the server to do something; its answer (``ASSIGNED``, ``ACCEPTED``).

        Raises:
            SessionRefusal: The server refused it, with its reason.
            SessionClosed: The session was closed before an answer came.
        """
        request = uuid.uuid4().hex
        full = {**frame, "request": request}
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request] = _Pending(full, future)
        if self._open.is_set():
            await self._send(full)
        try:
            return await future
        finally:
            self._pending.pop(request, None)
