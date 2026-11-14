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

A session also records the spec version that wrote it. Frames written by another version
are not sent: a server reads what the agent's current version speaks, and frames it
cannot read would be refused for good, on every start, by an agent that was upgraded
precisely to be let in. They stay in the file, and :meth:`RetainedFrames.set_aside` says
how many there are.
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

    def __init__(self, path: str | None, agent: str, spec_version: str | None = None) -> None:
        self.path = path
        self.agent = agent
        #: The spec version this process writes; None compares nothing (every session is sent).
        self.spec_version = spec_version
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
            # A file from before sessions said which spec wrote them: its sessions say none.
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(journal_sync)")}
            if "spec_version" not in columns:
                self._db.execute("ALTER TABLE journal_sync ADD COLUMN spec_version TEXT")

    def __repr__(self) -> str:
        return f"RetainedFrames(path={self.path!r}, agent={self.agent!r})"

    def open_session(self, session_id: str, created_at: float | None = None) -> float:
        """Know a session (its creation time orders the backlog); returns that time."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO journal_sync (agent, session_id, created_at, spec_version) VALUES (?, ?, ?, ?)",
                (
                    self.agent,
                    session_id,
                    time.time() if created_at is None else created_at,
                    self.spec_version,
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

    def set_aside(self) -> int:
        """How many retained frames were written by another spec version than this
        process's, and so are not sent."""
        if self.spec_version is None:
            return 0
        with self._lock:
            (count,) = self._db.execute(
                """
                SELECT COUNT(*) FROM journal j
                LEFT JOIN journal_sync s
                ON s.agent = j.agent AND s.session_id = j.session_id
                WHERE j.agent = ? AND j.pos > COALESCE(s.acked_pos, 0)
                AND s.spec_version IS NOT ?
                """,
                (self.agent, self.spec_version),
            ).fetchone()
        return int(count)

    def load(self) -> Iterator[StoredFrame]:
        """Every retained frame this process's spec version wrote, in ``(session created,
        pos)`` order."""
        with self._lock:
            rows = self._db.execute(
                """
                SELECT j.session_id, COALESCE(s.created_at, 0), j.pos, j.frame
                FROM journal j
                LEFT JOIN journal_sync s
                ON s.agent = j.agent AND s.session_id = j.session_id
                WHERE j.agent = ? AND j.pos > COALESCE(s.acked_pos, 0)
                AND (? IS NULL OR s.spec_version IS ?)
                ORDER BY COALESCE(s.created_at, 0), j.session_id, j.pos
                """,
                (self.agent, self.spec_version, self.spec_version),
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
