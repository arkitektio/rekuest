"""No-Docker checks that an assignment the agent receives can never go silent.

Each of these is a way a task used to stay non-terminal on the backend forever while the
agent looked perfectly healthy: the agent received the ``Assign`` and then — for a reason the
backend never learned about — simply never mentioned the task again.

They drive a real ``BaseAgent`` message loop over
:class:`arkitekt_runtime.local.MemoryAgentTransport`.
"""

import asyncio
from rekuest.agents.agent import RekuestAgent
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from arkitekt_runtime import messages
from arkitekt_runtime.agents.base import BaseAgent
from arkitekt_runtime.types import TaskEventKind
from rekuest.api.schema import TaskEventChange
from arkitekt_spec.declare.app import AppRegistry

from arkitekt_runtime.local import MemoryAgentTransport


# These states belong to a registry, as they would to an app. There is no
# process-wide one to fall into.
_REGISTRY = AppRegistry()


@_REGISTRY.state
@dataclass
class Stage:
    """A state some function depends on — and that the agent under test never initializes."""

    position: int = 0


@pytest.fixture()
def transport() -> MemoryAgentTransport:
    return MemoryAgentTransport()


@pytest.fixture()
def agent(transport: MemoryAgentTransport) -> BaseAgent:
    """A bare agent with its own registry, so nothing leaks between tests."""
    return RekuestAgent(
        name="never-hangs-test", transport=transport, app_registry=AppRegistry()
    )


async def _run_loop(agent: BaseAgent) -> AsyncIterator[None]:
    async for message in agent.transport.areceive():
        await agent.process(message)
        yield


async def _pump(agent: BaseAgent, count: int) -> None:
    loop = _run_loop(agent)
    for _ in range(count):
        await asyncio.wait_for(loop.__anext__(), timeout=2.0)


async def _until(predicate, timeout: float = 2.0) -> None:
    waited = 0.0
    while not predicate() and waited < timeout:
        await asyncio.sleep(0.01)
        waited += 0.01
    assert predicate(), "condition was not reached in time"


def _assign(task: str, interface: str, **args: object) -> messages.Assign:
    return messages.Assign(
        task=task,
        interface=interface,
        args=args,
        implementation="impl-1",
        action="action-1",
        reference=f"ref-{task}",
        user="user-1",
        org="org-1",
    )


def _register(agent: BaseAgent, function) -> None:
    agent.app_registry.register(
        function,
    )
    agent.collect_from_registry()




@pytest.mark.asyncio
async def test_duplicate_assign_for_finished_task_resends_its_report(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    """If the backend redelivers a finished task, what it is missing is the report."""

    async def aquick(x: int) -> int:
        """Return immediately."""
        return x

    _register(agent, aquick)
    await agent._adispatch(messages.SessionInit(session_id="s", states={}))

    transport.feed(_assign("task-2", "aquick", x=1))
    await _pump(agent, 1)
    await _until(lambda: len(transport.of_type(messages.Completed)) == 1)

    transport.feed(_assign("task-2", "aquick", x=1))  # never acked → redelivered
    await _pump(agent, 1)

    completed = transport.of_type(messages.Completed)
    assert len(completed) == 2, "the retained terminal report must be re-sent"
    assert completed[1].seq == completed[0].seq, "re-sent as-is, not re-executed"

    # Once acked the outcome is known to the backend: a further duplicate is just dropped.
    assert completed[0].pos is not None
    transport.feed(messages.JournalAck(journal_session="s", pos=completed[0].pos))
    transport.feed(_assign("task-2", "aquick", x=1))
    await _pump(agent, 2)
    await asyncio.sleep(0.05)
    assert len(transport.of_type(messages.Completed)) == 2






class _ScriptedPostman:
    """A postman whose ``aassign`` replays a fixed event script, then waits forever —
    exactly what the real one does once the backend has nothing more to say."""

    def __init__(self, *kinds: TaskEventKind) -> None:
        self.kinds = kinds

    async def aassign(self, **kwargs: object) -> AsyncIterator[TaskEventChange]:
        for index, kind in enumerate(self.kinds):
            yield TaskEventChange(
                id=str(index), task="t", kind=kind, createdAt="2026-01-01T00:00:00Z"
            )
        await asyncio.Event().wait()


