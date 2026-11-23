"""No-Docker checks for what the agent does with each inbound message.

These run a real ``BaseAgent`` message loop against
:class:`arkitekt_runtime.local.MemoryAgentTransport`, which is the first fake able to
drive it (the pre-existing ones had no ``areceive``). That is what makes the routing in
``BaseAgent.process`` testable rather than merely inspectable.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from arkitekt_runtime import messages
from arkitekt_runtime.agents.base import BaseAgent
from arkitekt_runtime.local import MemoryAgentTransport
from arkitekt_spec.declare.app import AppRegistry

from rekuest.agents.agent import RekuestAgent


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
    until a JOURNAL_ACK covers it.
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

    transport.feed(messages.JournalAck(journal_session="s", through=2))
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

    transport.feed(messages.CompletedEvent(task="delegated-1", event="ev-1"))
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
async def test_an_inquiry_is_answered_at_once_from_what_the_agent_knows(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    """Asked about a task, the agent says what it knows of it, without touching it: one it
    is on, one whose end it reported, and one it never had (which the backend then ends as
    lost, so it is the last thing the agent says, never a guess)."""
    release = asyncio.Event()

    async def slow(x: int) -> int:
        """Stay running until released."""
        await release.wait()
        return x

    agent.app_registry.register(
        slow,
    )
    agent.collect_from_registry()

    transport.feed(
        messages.Assign(
            task="task-1",
            interface="slow",
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
        if transport.of_type(messages.Started):
            break
        await asyncio.sleep(0)

    async def ask(task: str) -> messages.InquiryAnswer:
        transport.feed(messages.Inquire(task=task, request=f"q-{task}"))
        await _pump(agent, 1)
        answer = transport.of_type(messages.InquiryAnswer)[-1]
        assert answer.request == f"q-{task}"
        return answer

    assert (await ask("task-1")).status == messages.TaskStatus.RUNNING
    assert (await ask("never-had")).status == messages.TaskStatus.UNKNOWN

    # And what it last said of itself: how far it is, when it said so, and where it stands.
    await agent._adispatch(
        messages.Progress(task="task-1", progress=40, message="half way")
    )
    told = await ask("task-1")
    assert (told.progress, told.message) == (
        40,
        "half way",
    ) and told.reported_at is not None
    await agent._adispatch(messages.Paused(task="task-1"))
    assert (await ask("task-1")).status == messages.TaskStatus.PAUSED
    await agent._adispatch(messages.Resumed(task="task-1"))
    assert (await ask("task-1")).status == messages.TaskStatus.RUNNING

    release.set()
    for _ in range(50):
        if transport.of_type(messages.Completed):
            break
        await asyncio.sleep(0)
    assert (await ask("task-1")).status == messages.TaskStatus.ENDED
