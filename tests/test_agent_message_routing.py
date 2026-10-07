"""No-Docker checks for what the agent does with each inbound message.

These run a real ``BaseAgent`` message loop against
:class:`arkitekt_runtime.local.MemoryAgentTransport`, which is the first fake able to
drive it (the pre-existing ones had no ``areceive``). That is what makes the routing in
``BaseAgent.process`` testable rather than merely inspectable.
"""

import asyncio
from rekuest.agents.agent import RekuestAgent
from collections.abc import AsyncIterator

import pytest

from arkitekt_runtime import messages
from arkitekt_runtime.agents.base import BaseAgent
from arkitekt_spec.declare.app import AppRegistry


from arkitekt_runtime.local import MemoryAgentTransport


@pytest.fixture()
def transport() -> MemoryAgentTransport:
    return MemoryAgentTransport()


@pytest.fixture()
def agent(transport: MemoryAgentTransport) -> BaseAgent:
    """A bare agent with its own registry, so nothing leaks between tests."""
    return RekuestAgent(
        name="routing-test", transport=transport, app_registry=AppRegistry()
    )


async def _run_loop(agent: BaseAgent) -> AsyncIterator[None]:
    """Drive process() over the transport stream, as aloop does."""
    async for message in agent.transport.areceive():
        await agent.process(message)
        yield


async def _pump(agent: BaseAgent, count: int) -> None:
    """Process exactly `count` inbound messages."""
    loop = _run_loop(agent)
    for _ in range(count):
        await asyncio.wait_for(loop.__anext__(), timeout=2.0)


def _errors(transport: MemoryAgentTransport) -> list[str]:
    return [c.error for c in transport.of_type(messages.Critical)]






@pytest.mark.asyncio
async def test_init_releases_connect_and_resends_unacked_reports(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    """Init is the session signal: it acknowledges Register and re-sends retained frames.

    The backend re-sends Init on every connection, so this is also the only notice the
    agent gets that a dropped connection was recovered. A numbered report is retained
    until a JOURNAL_ACK covers it: an EVENT_ACK retires nothing.
    """
    await agent._adispatch(messages.SessionInit(session_id="s", states={}))
    agent.journal.begin_task("task-1")
    await agent._adispatch(messages.Completed(task="task-1"))
    completed = transport.of_type(messages.Completed)
    assert len(completed) == 1 and (completed[0].pos, completed[0].task_step) == (2, 1)
    assert agent.retained, "a numbered report must be retained pending its ack"

    transport.feed(messages.Init(agent="agent-1"))
    await _pump(agent, 1)

    assert agent._connected_event.is_set(), "Init must release anyone awaiting aconnect"
    resent = transport.of_type(messages.Completed)
    assert len(resent) == 2, "the unacked report must be resent on the new connection"
    assert resent[1] == resent[0], "the resend is the frame as it was (seq, pos, step)"

    transport.feed(messages.EventAck(event=completed[0].id))
    await _pump(agent, 1)
    assert agent.retained, "only a JOURNAL_ACK retires a numbered frame"

    transport.feed(messages.JournalAck(journal_session="s", pos=2))
    await _pump(agent, 1)
    assert not agent.retained

    transport.feed(messages.Init(agent="agent-1"))
    await _pump(agent, 1)
    assert len(transport.of_type(messages.Completed)) == 2, (
        "an acked report must not replay"
    )


@pytest.mark.asyncio
async def test_caller_answers_go_to_the_postman_not_the_actors(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    """Answers to delegated work are a different concern from assigned work.

    They are keyed by the caller's request id and must never be looked up in
    managed_assignments, which only ever holds work assigned *to* this agent.
    """
    seen: list[messages.ExecutionEvent] = []
    agent.caller_postman.handle_execution_event = seen.append  # type: ignore[method-assign]

    transport.feed(messages.CompletedEvent(task="delegated-1", event="ev-1", seq=1))
    await _pump(agent, 1)

    assert len(seen) == 1, "an execution-event mirror belongs to the caller postman"
    assert "delegated-1" not in agent.managed_assignments






class _DeadSocket(MemoryAgentTransport):
    """A transport whose socket has gone away underneath it."""

    async def asend(self, message: messages.FromAgentMessage) -> None:
        """Fail the way a lost connection does."""
        raise ConnectionResetError("socket is gone")








@pytest.mark.asyncio
async def test_probe_response_is_routed_to_the_caller_postman(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    """A ProbeResponse answers work this agent originated, like an AssignResponse."""
    seen: list[messages.ProbeResponse] = []
    agent.caller_postman.handle_probe_response = seen.append  # type: ignore[method-assign]

    transport.feed(messages.ProbeResponse(request="req-1", probe="p-1"))
    await _pump(agent, 1)

    assert len(seen) == 1 and seen[0].probe == "p-1"






@pytest.mark.asyncio
async def test_a_liveness_inquiry_does_not_contradict_a_replayed_report(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    """Init replays retained terminal reports and *then* answers inquiries.

    Both describe the same task, so the inquiry must defer: the replayed report
    already says exactly how the task ended.
    """

    async def quick(x: int) -> int:
        """Finish immediately."""
        return x

    agent.app_registry.register(
        quick,
    )
    agent.collect_from_registry()

    transport.feed(
        messages.Assign(
            task="task-1",
            interface="quick",
            args={"x": 1},
            implementation="impl-1",
            action="action-1",
            reference="ref-1",
            user="user-1",
            org="org-1",
        )
    )
    await _pump(agent, 1)
    for _ in range(50):
        if transport.of_type(messages.Completed):
            break
        await asyncio.sleep(0)

    def _liveness_answers() -> list[messages.FromAgentMessage]:
        """Anything the agent says about task-1 other than replaying its report."""
        return [
            m
            for m in transport.sent
            if isinstance(m, (messages.Progress, messages.Critical))
            and getattr(m, "task", None) == "task-1"
        ]

    before = len(_liveness_answers())
    transport.feed(
        messages.Init(
            agent="agent-1",
            inquiries=[messages.AssignInquiry(task="task-1")],
        )
    )
    await _pump(agent, 1)

    assert len(_liveness_answers()) == before, (
        "the inquiry must defer to the replayed report, but the agent also said "
        f"{[m for m in _liveness_answers()[before:]]}"
    )




