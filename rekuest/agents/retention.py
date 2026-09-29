"""The numbered frames a distributed agent still owes its server, kept on disk.

Every numbered frame (``pos``) is retained until a ``JOURNAL_ACK`` covers it, and it
must survive a restart of the process: the next process sends the earlier sessions'
unacked frames first, in ``(session created, pos)`` order, after its ``INIT`` (see
rekuest's ``docs/design/journal.md``).

A small SQLite file (stdlib ``sqlite3``) with the same two tables the Rust agent keeps:

- ``journal (agent, session_id, pos, frame)``: the retained frames, as sent;
- ``journal_sync (agent, session_id, created_at, acked_pos)``: per session, when it
  was created and how far the server acknowledged it (only ever raised).

Rows are scoped by ``agent`` (the agent's name), so apps sharing a working directory
never send each other's frames. An acked frame is deleted at once.
"""

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Where the file is, if set; else :data:`DEFAULT_JOURNAL_PATH` in the working directory.
JOURNAL_PATH_ENV = "REKUEST_JOURNAL_PATH"
DEFAULT_JOURNAL_PATH = os.path.join(".arkitekt", "rekuest_journal.db")


def default_journal_path() -> str:
    """``$REKUEST_JOURNAL_PATH``, else ``.arkitekt/rekuest_journal.db``."""
    return os.environ.get(JOURNAL_PATH_ENV) or DEFAULT_JOURNAL_PATH


@dataclass(frozen=True)
class StoredFrame:
    """One retained frame, as it was sent."""

    session_id: str
    created_at: float
    pos: int
    frame: str


class RetainedFrames:
    """The retained frames of one agent, in a SQLite file (``None``: in memory only)."""

    def __init__(self, path: str | None, agent: str) -> None:
        self.path = path
        self.agent = agent
        self._lock = threading.Lock()
        if path is not None and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path or ":memory:", check_same_thread=False)
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS journal (
                    agent TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    pos INTEGER NOT NULL,
                    frame TEXT NOT NULL,
                    PRIMARY KEY (agent, session_id, pos)
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS journal_sync (
                    agent TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    acked_pos INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (agent, session_id)
                )
                """
            )

    def __repr__(self) -> str:
        return f"RetainedFrames(path={self.path!r}, agent={self.agent!r})"

    def open_session(self, session_id: str, created_at: float | None = None) -> float:
        """Know a session (its creation time orders the backlog); returns that time."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO journal_sync (agent, session_id, created_at) VALUES (?, ?, ?)",
                (
                    self.agent,
                    session_id,
                    time.time() if created_at is None else created_at,
                ),
            )
            row = self._db.execute(
                "SELECT created_at FROM journal_sync WHERE agent = ? AND session_id = ?",
                (self.agent, session_id),
            ).fetchone()
        return float(row[0])

    def add(self, session_id: str, pos: int, frame: str) -> None:
        """Retain a frame (a re-sent one replaces itself)."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO journal (agent, session_id, pos, frame) VALUES (?, ?, ?, ?)",
                (self.agent, session_id, pos, frame),
            )

    def ack(self, session_id: str, pos: int) -> None:
        """Everything of ``session_id`` up to ``pos`` is acknowledged: drop it."""
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM journal WHERE agent = ? AND session_id = ? AND pos <= ?",
                (self.agent, session_id, pos),
            )
            self._db.execute(
                "UPDATE journal_sync SET acked_pos = MAX(acked_pos, ?) WHERE agent = ? AND session_id = ?",
                (pos, self.agent, session_id),
            )

    def prune(self, keep: str | None = None) -> None:
        """Forget sessions with nothing left to send (but ``keep``)."""
        with self._lock, self._db:
            self._db.execute(
                """
                DELETE FROM journal_sync
                WHERE agent = ? AND session_id != ? AND NOT EXISTS (
                    SELECT 1 FROM journal
                    WHERE journal.agent = journal_sync.agent
                    AND journal.session_id = journal_sync.session_id
                )
                """,
                (self.agent, keep or ""),
            )

    def load(self) -> Iterator[StoredFrame]:
        """Every retained frame, in ``(session created, pos)`` order."""
        with self._lock:
            rows = self._db.execute(
                """
                SELECT j.session_id, COALESCE(s.created_at, 0), j.pos, j.frame
                FROM journal j
                LEFT JOIN journal_sync s
                ON s.agent = j.agent AND s.session_id = j.session_id
                WHERE j.agent = ? AND j.pos > COALESCE(s.acked_pos, 0)
                ORDER BY COALESCE(s.created_at, 0), j.session_id, j.pos
                """,
                (self.agent,),
            ).fetchall()
        for session_id, created_at, pos, frame in rows:
            yield StoredFrame(session_id, float(created_at), int(pos), frame)

    def close(self) -> None:
        with self._lock:
            self._db.close()


__all__ = [
    "DEFAULT_JOURNAL_PATH",
    "JOURNAL_PATH_ENV",
    "RetainedFrames",
    "StoredFrame",
    "default_journal_path",
]
