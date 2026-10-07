"""The distributed agent's side of the agent-report contract (``docs/design/journal.md``):
every numbered frame retained until ``JOURNAL_ACK``, across a session change and a
restart; nothing sent between ``REGISTER`` and ``INIT``; the resend order after it.

The numbering itself (``pos``, ``task_step``, the task gate) is tested in arkitekt-runtime.
"""

import asyncio
import contextlib
import json
import time
from pathlib import Path

import pytest
import websockets
from arkitekt_runtime import messages
from arkitekt_runtime.agents.base import BaseAgent
from arkitekt_runtime.agents.journal import TaskGate
from arkitekt_runtime.agents.policy import ConnectionPolicy
from arkitekt_spec.declare.app import AppRegistry

from rekuest.agents.agent import RekuestAgent
from rekuest.agents.caller import AgentPostman
from rekuest.agents.transport.websocket import WebsocketAgentTransport

from arkitekt_runtime.local import MemoryAgentTransport
from .test_transport_lifecycle import (
    _NO_DELAY,
    FakeConnect,
    FakeSocket,
    _Host,
    _stop,
    _token,
)


def _session_init(session: str) -> messages.SessionInit:
    return messages.SessionInit(session_id=session, states={"Camera": {"exposure": 1, "tags": []}})


def _patch(rev: int, task: str | None, op: str = "replace", path: str = "/exposure", value: object = None) -> messages.StatePatch:
    return messages.StatePatch(
        session_id="s",
        global_rev=rev,
        state_name="Camera",
        ts=0.0,
        op=op,
        path=path,
        value=rev + 1 if value is None else value,
        old_value=None,
        task_id=task,
    )


async def _until(predicate, timeout: float = 2.0) -> None:  # noqa: ANN001
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


# ---------------------------------------------------------- remote retention --


@pytest.fixture()
def transport() -> MemoryAgentTransport:
    return MemoryAgentTransport()


@pytest.fixture()
def agent(transport: MemoryAgentTransport) -> RekuestAgent:
    return RekuestAgent(name="journal-test", transport=transport, app_registry=AppRegistry())


async def _process(agent: BaseAgent, message: messages.ToAgentMessage) -> None:
    await agent.process(message)


@pytest.mark.asyncio
async def test_journaled_frames_carry_their_position(
    agent: RekuestAgent, transport: MemoryAgentTransport
) -> None:
    await agent._adispatch(messages.Progress(task="before"))
    await agent._adispatch(_session_init(agent.current_session).model_copy(update={"session_id": "s"}))
    agent.journal.begin_task("t")
    await agent._adispatch(messages.Log(task="t", message="a"))
    await agent._adispatch(messages.Log(task="other", message="not ours"))

    early, init, log, other = transport.sent
    assert early.pos is None, "nothing is recorded before the session"  # type: ignore[union-attr]
    assert (init.pos, init.journal_session, init.task_step) == (1, "s", None)  # type: ignore[union-attr]
    assert (log.pos, log.journal_session, log.seq, log.task_step) == (2, "s", 2, 1)  # type: ignore[union-attr]
    assert (other.pos, other.task_step) == (3, None), "a task this process never ran"  # type: ignore[union-attr]
    assert log.agent_ts is not None and abs(log.agent_ts - time.time()) < 60  # type: ignore[union-attr]
    wire = json.loads(log.model_dump_json())
    assert (wire["pos"], wire["journal_session"], wire["task_step"]) == (2, "s", 1)


@pytest.mark.asyncio
async def test_every_numbered_frame_is_retained_until_a_journal_ack_and_resent_in_order(
    agent: RekuestAgent, transport: MemoryAgentTransport
) -> None:
    await _process(agent, messages.Init(agent="a"))
    await agent._adispatch(_session_init("s"))  # pos 1
    await agent._adispatch(messages.Log(task="t", message="a"))  # pos 2
    await agent._adispatch(messages.Yield(task="t", returns={}))  # pos 3
    await _process(agent, messages.JournalAck(journal_session="s", pos=1))
    await agent._adispatch(messages.Completed(task="t"))  # pos 4
    assert [m.pos for m in agent.retained] == [2, 3, 4]  # type: ignore[union-attr]
    first_sent = {m.pos: m for m in transport.sent}  # type: ignore[union-attr]

    # Reconnect: everything not acked goes out again, once each, in order, as it was.
    transport.sent.clear()
    await _process(agent, messages.Init(agent="a"))
    assert [m.pos for m in transport.sent] == [2, 3, 4]  # type: ignore[union-attr]
    assert transport.sent == [first_sent[p] for p in (2, 3, 4)]

    # The terminal report too retires on the JOURNAL_ACK alone.
    await _process(agent, messages.EventAck(event=first_sent[4].id))
    assert [m.pos for m in agent.retained] == [2, 3, 4]  # type: ignore[union-attr]
    await _process(agent, messages.JournalAck(journal_session="s", pos=4))
    assert not agent.retained


@pytest.mark.asyncio
async def test_a_probes_reports_are_neither_numbered_nor_retained(
    agent: RekuestAgent, transport: MemoryAgentTransport
) -> None:
    await agent._adispatch(_session_init("s"))
    await agent._adispatch(messages.Log(task="p-1", message="probing"))
    await agent._adispatch(messages.Completed(task="p-1"))
    await agent._adispatch(_patch(1, "p-1"))
    log, completed = transport.sent[1:3]
    assert log.pos is None and completed.pos is None  # type: ignore[union-attr]
    patch = transport.sent[3]
    assert (patch.pos, patch.task_step, patch.task_id) == (2, None, "p-1")  # type: ignore[union-attr]
    assert [m.pos for m in agent.retained] == [1, 2], "the state changed all the same"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_new_session_keeps_the_earlier_sessions_frames_and_sends_them_first(
    agent: RekuestAgent, transport: MemoryAgentTransport
) -> None:
    await agent._adispatch(_session_init("old"))
    await agent._adispatch(messages.Log(task="t", message="of the old session"))
    await asyncio.sleep(0.01)  # the new session is created later
    await agent._adispatch(_session_init("new"))
    await agent._adispatch(messages.Log(task="t", message="of the new session"))

    transport.sent.clear()
    await _process(agent, messages.Init(agent="a"))
    assert [(m.journal_session, m.pos) for m in transport.sent] == [  # type: ignore[union-attr]
        ("old", 1),
        ("old", 2),
        ("new", 1),
        ("new", 2),
    ]


@pytest.mark.asyncio
async def test_retained_frames_survive_a_restart(tmp_path: Path) -> None:
    """The next process sends the earlier sessions' unacked frames first, in
    ``(session created, pos)`` order, each with its own ``journal_session``; then its own."""
    path = str(tmp_path / "journal.db")
    before = MemoryAgentTransport()
    dying = RekuestAgent(name="restart", transport=before, app_registry=AppRegistry(), journal_path=path)
    await dying._adispatch(_session_init("first"))
    await dying._adispatch(messages.Log(task="t", message="one"))
    await dying._adispatch(messages.Completed(task="t"))
    await dying.process(messages.JournalAck(journal_session="first", pos=1))
    await dying.atear_down()  # the process ends; pos 2 and 3 were never acked

    after = MemoryAgentTransport()
    agent = RekuestAgent(name="restart", transport=after, app_registry=AppRegistry(), journal_path=path)
    other = RekuestAgent(name="another-app", transport=MemoryAgentTransport(), app_registry=AppRegistry(), journal_path=path)
    await agent.astart()
    await other.astart()
    assert not other.retained, "never another agent's frames"

    await agent.process(messages.Init(agent="a"))
    await agent._adispatch(_session_init(agent.current_session))
    resent = [(m.journal_session, m.pos, m.type) for m in after.sent]  # type: ignore[union-attr]
    assert resent == [
        ("first", 2, "LOG"),
        ("first", 3, "COMPLETED"),
        (agent.current_session, 1, "SESSION_INIT"),
    ]
    assert after.sent[1] == before.sent[2], "as it was sent"

    await agent.process(messages.JournalAck(journal_session="first", pos=3))
    await agent.atear_down()
    again = RekuestAgent(name="restart", transport=MemoryAgentTransport(), app_registry=AppRegistry(), journal_path=path)
    await again.astart()
    assert [(m.journal_session, m.pos) for m in again.retained] == [(agent.current_session, 1)]  # type: ignore[union-attr]
    await again.atear_down()
    await other.atear_down()


@pytest.mark.asyncio
async def test_nothing_is_reported_for_a_task_after_its_end(
    agent: RekuestAgent, transport: MemoryAgentTransport
) -> None:
    gate = agent._task_gates["t"] = TaskGate()
    await agent._adispatch(messages.Log(task="t", message="a"))
    await agent._adispatch(messages.Cancelled(task="t"))
    await agent._adispatch(messages.Log(task="t", message="late"))
    await agent._adispatch(messages.Completed(task="t"))
    await agent._adispatch(messages.Unlock(key="cam", task="t"))
    assert gate.closed
    assert [m.type for m in transport.sent] == ["LOG", "CANCELLED", "UNLOCK"], (
        "only the lock release follows the end"
    )


@pytest.mark.asyncio
async def test_a_child_call_carries_its_parents_next_step(
    agent: RekuestAgent, transport: MemoryAgentTransport
) -> None:
    """``ASSIGN_REQUEST.parent_step``: the step is taken after what the task reported
    before the call, and the next report takes the one after it. The reference stays
    the caller's own."""
    await agent._adispatch(_session_init("s"))
    agent.journal.begin_task("42")
    agent._task_gates["42"] = TaskGate()
    await agent._adispatch(messages.Log(task="42", message="before"))
    postman = agent.caller_postman
    assert isinstance(postman, AgentPostman)

    calling = asyncio.create_task(
        anext(postman.aassign(args={"x": 1}, action="7", parent="42", reference="mine"))  # type: ignore[arg-type]
    )
    await _until(lambda: transport.of_type(messages.AssignRequest))
    (request,) = transport.of_type(messages.AssignRequest)
    assert (request.reference, request.parent, request.parent_step) == ("mine", "42", 2)
    assert "pos" not in json.loads(request.model_dump_json()), "not numbered"
    await agent._adispatch(messages.Log(task="42", message="after"))
    assert [m.task_step for m in transport.of_type(messages.Log)] == [1, 3]

    # A root call takes no step.
    root = asyncio.create_task(anext(postman.aassign(args={}, action="7")))  # type: ignore[arg-type]
    await _until(lambda: len(transport.of_type(messages.AssignRequest)) == 2)
    root_request = transport.of_type(messages.AssignRequest)[1]
    assert root_request.parent_step is None and root_request.reference
    for task in (calling, root):
        await _stop(task)


# --------------------------------------------------------------- transport --


@pytest.mark.asyncio
async def test_a_frame_whose_send_failed_goes_out_first_after_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failed frame used to be put back at the tail, behind what came after it."""
    first, second = FakeSocket(), FakeSocket()
    sockets = iter([first, second])
    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: FakeConnect(next(sockets)))

    transport = WebsocketAgentTransport(endpoint_url="ws://localhost:8000/agi", token_loader=_token)
    transport.set_transport_host(_Host(ConnectionPolicy(backoff=_NO_DELAY)))

    async with transport as transport:
        await transport.aconnect()

        async def consume() -> None:
            async for _ in transport.areceive():
                pass

        consumer = asyncio.create_task(consume())
        first.feed(messages.Init(agent="agent-1"))
        await asyncio.sleep(0.05)

        await transport.asend(messages.Log(task="t", message="one"))
        await transport.asend(messages.Log(task="t", message="two"))
        await asyncio.sleep(0.005)
        first.drop()
        await asyncio.sleep(0.2)
        second.feed(messages.Init(agent="agent-1"))
        await asyncio.sleep(0.1)

        logs = [json.loads(m)["message"] for m in second.sent if '"LOG"' in m]
        assert logs == ["one", "two"], f"a drop must not reorder what was sent, got {logs}"

        await transport.adisconnect()
        await _stop(consumer)


def _frames(socket: FakeSocket) -> list[dict[str, object]]:
    return [json.loads(frame) for frame in socket.sent]


@pytest.mark.asyncio
async def test_nothing_is_sent_between_register_and_init_and_retained_frames_go_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real agent over the real transport: across a drop, the new connection sends
    its ``REGISTER``, then nothing until ``INIT``, then every unacked frame once, in
    ``pos`` order (what the transport still queued is dropped there and re-sent)."""
    first, second = FakeSocket(), FakeSocket()
    sockets = iter([first, second])
    monkeypatch.setattr(websockets, "connect", lambda *args, **kwargs: FakeConnect(next(sockets)))

    transport = WebsocketAgentTransport(endpoint_url="ws://localhost:8000/agi", token_loader=_token)
    agent = RekuestAgent(
        name="wire-test",
        transport=transport,
        app_registry=AppRegistry(),
        connection_policy=ConnectionPolicy(backoff=_NO_DELAY),
    )
    async with agent:
        connecting = asyncio.create_task(agent.aconnect(timeout=5.0))
        await _until(lambda: first.sent)
        await asyncio.sleep(0.05)
        assert [f["type"] for f in _frames(first)] == ["REGISTER"], "held until INIT"
        first.feed(messages.Init(agent="agent-1"))
        await connecting
        await _until(lambda: any(f["type"] == "SESSION_INIT" for f in _frames(first)))

        await agent._adispatch(messages.Log(task="t", message="acked by nobody"))  # pos 2
        await _until(lambda: len(_frames(first)) == 3)
        first.drop()
        await _until(lambda: not transport.connected)
        await agent._adispatch(messages.Log(task="t", message="while down"))  # pos 3

        await _until(lambda: second.sent)
        await asyncio.sleep(0.05)
        assert [f["type"] for f in _frames(second)] == ["REGISTER"], (
            "nothing between REGISTER and INIT"
        )
        second.feed(messages.Init(agent="agent-1"))
        await _until(lambda: len(_frames(second)) >= 4)
        await asyncio.sleep(0.05)
        sent = _frames(second)[1:]
        assert [(f["type"], f["pos"]) for f in sent] == [
            ("SESSION_INIT", 1),
            ("LOG", 2),
            ("LOG", 3),
        ], "each unacked frame once, in pos order"

        second.feed(messages.JournalAck(journal_session=agent.current_session, pos=3))
        await _until(lambda: not agent.retained)
        await agent.atear_down()


@pytest.mark.asyncio
async def test_reports_queue_behind_a_patch_whose_shrink_is_blocked(
    agent: RekuestAgent, transport: MemoryAgentTransport
) -> None:
    """A report dispatched while the processor is stuck shrinking a patch returns at
    once, but is queued behind that patch rather than sent around it: once the
    shrink finishes, the patch goes out first, then the reports in their order."""
    import janus
    from arkitekt_runtime.agents.dataclasses import QueuedPatchEvent
    from arkitekt_spec.declare.state.publish import Patch

    replied = asyncio.Event()

    async def shrink_waiting_for_a_reply(queued: QueuedPatchEvent) -> None:
        await replied.wait()
        await agent._aemit(_patch(1, "t"))

    agent._aprocess_patch_event = shrink_waiting_for_a_reply  # type: ignore[method-assign]
    agent._event_queue = janus.Queue()
    agent._patch_processor_task = asyncio.create_task(agent.apatch_event_loop())
    try:
        agent.publish_patch("Camera", Patch(op="replace", path="/exposure", value=2))
        await asyncio.wait_for(agent._adispatch(messages.Log(task="t", message="after")), 1.0)
        await asyncio.wait_for(agent._adispatch(messages.Unlock(key="cam", task="t")), 1.0)
        assert transport.sent == [], "queued behind the patch, not sent around it"

        replied.set()  # the shrink the processor was stuck in finishes
        await asyncio.wait_for(agent._event_queue.async_q.join(), 1.0)
        assert [m.type for m in transport.sent] == ["STATE_PATCH", "LOG", "UNLOCK"]
    finally:
        agent._patch_processor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await agent._patch_processor_task
        queue, agent._event_queue = agent._event_queue, None
        queue.close()


@pytest.mark.asyncio
async def test_an_unwritable_store_keeps_the_frames_in_memory(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")
    transport = MemoryAgentTransport()
    agent = RekuestAgent(
        name="unwritable",
        transport=transport,
        app_registry=AppRegistry(),
        journal_path=str(blocker / "journal.db"),
    )
    await agent.astart()
    await agent._adispatch(_session_init("s"))
    assert [m.pos for m in agent.retained] == [1]  # type: ignore[union-attr]
    transport.sent.clear()
    await agent.process(messages.Init(agent="a"))
    assert [m.pos for m in transport.sent] == [1]  # type: ignore[union-attr]
    await agent.atear_down()
