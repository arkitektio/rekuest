"""A resumed workflow gets back even the values its dead process never got to send.

The server builds the resume journal from what it received. A value the dead process took
but had not sent (still in its journal on disk, resent only after ``Init``) is missing from
it. The successor has it, and completes the journal before the task runs.
"""

import time
from pathlib import Path

from arkitekt_runtime import messages
from arkitekt_spec.declare.app import AppRegistry
from rekuest.agents.agent import RekuestAgent
from rekuest.agents.retention import RetainedFrames

from .memory_transport import MemoryAgentTransport

NAME = "resume-agent"


def _retain(path: Path, *frames: messages.FromAgentMessage) -> None:
    """What a dead process left unacknowledged in its journal."""
    store = RetainedFrames(str(path), agent=NAME)
    store.open_session("dead", time.time())
    for pos, frame in enumerate(frames, start=1):
        store.add("dead", pos, frame.model_dump_json())


def _assign(effects: list[dict], last_step: int) -> messages.Assign:
    return messages.Assign(
        task="42", interface="i", args={}, implementation="impl", action="a", user="u", org="o",
        resume={"last_step": last_step, "effects": effects},
    )


def _agent(path: Path) -> RekuestAgent:
    return RekuestAgent(name=NAME, transport=MemoryAgentTransport(), app_registry=AppRegistry(), journal_path=str(path))


def test_an_unsent_effect_joins_the_journal(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    _retain(
        path,
        messages.Effect(task="42", effect="RANDOM", value="abcd", key="RANDOM:1", pos=1, journal_session="dead", task_step=4),
        messages.Effect(task="99", effect="NOW", value=5.0, key="NOW:1", pos=2, journal_session="dead", task_step=1),
    )

    completed = _agent(path)._complete_resume(_assign([{"key": "NOW:1", "effect": "NOW", "value": 1.0}], last_step=3))

    assert [(e.key, e.value) for e in completed.resume.effects] == [("NOW:1", 1.0), ("RANDOM:1", "abcd")]
    assert completed.resume.last_step == 4, "its step counts, so no step is numbered twice"


def test_what_the_server_already_has_wins(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    _retain(path, messages.Effect(task="42", effect="NOW", value=2.0, key="NOW:1", pos=1, journal_session="dead", task_step=2))

    completed = _agent(path)._complete_resume(_assign([{"key": "NOW:1", "effect": "NOW", "value": 1.0}], last_step=2))

    assert [(e.key, e.value) for e in completed.resume.effects] == [("NOW:1", 1.0)]


def test_nothing_held_changes_nothing(tmp_path: Path) -> None:
    message = _assign([], last_step=0)

    assert _agent(tmp_path / "journal.db")._complete_resume(message) is message
