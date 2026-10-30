"""The last value of each state an app declared ``persist`` for, kept on disk.

A startup hook of the next run takes it as ``old: SomeState | None``. It says what the
agent last knew, which is not always what the hardware did.

A file of its own beside the journal, and only when a state opts in. SQLite has one write
lock per file and a commit holds it through its fsync, so sharing the journal's file would
block every frame the agent retains for as long as a flush takes.

One row per state: nothing here grows. Rows are scoped by ``agent`` (the agent's name, so
by version too) like the retained frames, and ``schema_hash`` tells a value of a state whose
ports have changed since.
"""

import hashlib
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from arkitekt_spec.actions import StateDefinitionInput

logger = logging.getLogger(__name__)

STATE_FILE_NAME = "rekuest_state.db"


def state_store_path(journal_path: str) -> str:
    """The file beside the journal."""
    return os.path.join(os.path.dirname(journal_path), STATE_FILE_NAME)


def schema_hash(schema: StateDefinitionInput) -> str:
    """What a stored value's ports were."""
    return hashlib.sha256(schema.model_dump_json().encode()).hexdigest()


@dataclass(frozen=True)
class StoredState:
    """One state's last persisted value, its shrunk JSON as text."""

    state_name: str
    schema_hash: str
    session_id: str
    global_rev: int
    value: str


class PersistedStates:
    """The persisted states of one agent, in a SQLite file. Every commit is fsynced."""

    def __init__(self, path: str, agent: str) -> None:
        self.path = path
        self.agent = agent
        self._lock = threading.Lock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS last_state (
                    agent TEXT NOT NULL,
                    state_name TEXT NOT NULL,
                    schema_hash TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    global_rev INTEGER NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (agent, state_name)
                )
                """
            )

    def __repr__(self) -> str:
        return f"PersistedStates(path={self.path!r}, agent={self.agent!r})"

    def put(self, state: StoredState) -> None:
        """Keep a state's value, in place of the one before."""
        with self._lock, self._db:
            self._db.execute(
                """
                INSERT OR REPLACE INTO last_state
                (agent, state_name, schema_hash, session_id, global_rev, value)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self.agent,
                    state.state_name,
                    state.schema_hash,
                    state.session_id,
                    state.global_rev,
                    state.value,
                ),
            )

    def load(self) -> dict[str, StoredState]:
        """Every state's last value, by state."""
        with self._lock:
            rows = self._db.execute(
                """
                SELECT state_name, schema_hash, session_id, global_rev, value
                FROM last_state WHERE agent = ?
                """,
                (self.agent,),
            ).fetchall()
        return {row[0]: StoredState(*row) for row in rows}

    def close(self) -> None:
        with self._lock:
            self._db.close()


__all__ = [
    "PersistedStates",
    "STATE_FILE_NAME",
    "StoredState",
    "schema_hash",
    "state_store_path",
]
