"""The distributed agent's side of the journal: retention until ``JOURNAL_ACK`` and the
send order across a drop.

The journal itself (numbering, folding, the task gate) is tested in arkitekt-runtime.
"""

import asyncio
import contextlib
import json
import time

import pytest
import websockets
from arkitekt_runtime import messages
from arkitekt_runtime.agents.base import BaseAgent
from arkitekt_runtime.agents.journal import (
    TaskGate,
)
from arkitekt_runtime.agents.policy import ConnectionPolicy
from arkitekt_spec.declare.app import AppRegistry

from rekuest.agents.agent import RekuestAgent
from rekuest.agents.transport.websocket import WebsocketAgentTransport

from .memory_transport import MemoryAgentTransport
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



# ---------------------------------------------------------- remote retention --


@pytest.fixture()
def transport() -> MemoryAgentTransport:
    return MemoryAgentTransport()


@pytest.fixture()
def agent(transport: MemoryAgentTransport) -> BaseAgent:
    return RekuestAgent(name="journal-test", transport=transport, app_registry=AppRegistry())


async def _process(agent: BaseAgent, message: messages.ToAgentMessage) -> None:
    await agent.process(message)


@pytest.mark.asyncio
async def test_journaled_frames_carry_their_position(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    await agent._adispatch(messages.Progress(task="before"))
    await agent._adispatch(_session_init(agent.current_session).model_copy(update={"session_id": "s"}))
    await agent._adispatch(messages.Log(task="t", message="a"))

    early, init, log = transport.sent
    assert early.pos is None, "nothing is recorded before the session"  # type: ignore[union-attr]
    assert (init.pos, init.journal_session) == (1, "s")  # type: ignore[union-attr]
    assert (log.pos, log.journal_session, log.seq) == (2, "s", 2)  # type: ignore[union-attr]
    assert log.agent_ts is not None and abs(log.agent_ts - time.time()) < 60  # type: ignore[union-attr]
    wire = json.loads(log.model_dump_json())
    assert (wire["pos"], wire["journal_session"]) == (2, "s")


@pytest.mark.asyncio
async def test_without_journal_acks_only_terminals_are_retained(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    await _process(agent, messages.Init(agent="a"))
    await agent._adispatch(_session_init("s"))
    await agent._adispatch(messages.Log(task="t", message="a"))
    await agent._adispatch(messages.Completed(task="t"))
    assert not agent._retained
    assert len(agent._unacked_events) == 1


@pytest.mark.asyncio
async def test_journal_mode_retains_until_acked_and_resends_in_order(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    await _process(agent, messages.Init(agent="a", journal=True))
    await agent._adispatch(_session_init("s"))  # pos 1
    await agent._adispatch(messages.Log(task="t", message="a"))  # pos 2
    await agent._adispatch(messages.Yield(task="t", returns={}))  # pos 3
    await _process(agent, messages.JournalAck(journal_session="s", pos=1))
    await agent._adispatch(messages.Completed(task="t"))  # pos 4
    assert sorted(agent._retained) == [2, 3, 4]
    first_sent = {m.pos: m for m in transport.sent}  # type: ignore[union-attr]

    # Reconnect: everything not acked goes out again, once each, in order, as it was.
    transport.sent.clear()
    await _process(agent, messages.Init(agent="a", journal=True))
    assert [m.pos for m in transport.sent] == [2, 3, 4]  # type: ignore[union-attr]
    assert [m.seq for m in transport.sent] == [first_sent[p].seq for p in (2, 3, 4)]  # type: ignore[union-attr]

    await _process(agent, messages.JournalAck(journal_session="s", pos=4))
    assert not agent._retained
    assert not agent._unacked_events, "a JOURNAL_ACK also covers the terminal report"


@pytest.mark.asyncio
async def test_a_server_without_journal_drops_the_retained_frames(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    await _process(agent, messages.Init(agent="a", journal=True))
    await agent._adispatch(_session_init("s"))
    await agent._adispatch(messages.Log(task="t", message="a"))
    assert agent._retained
    await _process(agent, messages.Init(agent="a"))
    assert not agent._retained


@pytest.mark.asyncio
async def test_nothing_is_reported_for_a_task_after_its_end(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    gate = agent._task_gates["t"] = TaskGate()
    await agent._adispatch(messages.Log(task="t", message="a"))
    await agent._adispatch(messages.Cancelled(task="t"))
    await agent._adispatch(messages.Log(task="t", message="late"))
    await agent._adispatch(messages.Completed(task="t"))
    await agent._adispatch(messages.Unlock(key="cam"))
    assert gate.closed
    assert [m.type for m in transport.sent] == ["LOG", "CANCELLED", "UNLOCK"], (
        "only the lock release follows the end"
    )


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


@pytest.mark.asyncio
async def test_reporting_never_waits_on_the_processor(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    """The processor may wait on the message loop (shrinking a patch value can shelve
    over the socket, and the reply comes through ``process``), so the message loop's
    reports must not wait on the processor, or both wait forever."""
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
        await asyncio.wait_for(agent._adispatch(messages.Unlock(key="cam")), 1.0)
        assert transport.sent == [], "queued behind the patch, not sent around it"

        replied.set()  # the reply the processor was waiting for
        await asyncio.wait_for(agent._event_queue.async_q.join(), 1.0)
        assert [m.type for m in transport.sent] == ["STATE_PATCH", "LOG", "UNLOCK"]
    finally:
        agent._patch_processor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await agent._patch_processor_task
        queue, agent._event_queue = agent._event_queue, None
        queue.close()
