"""No-Docker checks for what a disconnect does to in-flight work.

The window these cover could not previously be observed at all. The websocket
transport retries a dropped socket transparently, so ``areceive()`` never ends and
the agent above it was never told control had been lost — an action that is only
safe while it can be cancelled just kept running. These drive that window through
:class:`tests.memory_transport.MemoryAgentTransport`, whose ``drop_link`` models
exactly it: the socket is gone, the stream is not.
"""

import asyncio
from rekuest.agents.agent import RekuestAgent
from collections.abc import AsyncIterator

import pytest

from arkitekt_runtime import messages
from arkitekt_spec.declare.actors.policy import (
    CancelOnDisconnect,
)
from arkitekt_runtime.agents.base import BaseAgent
from arkitekt_spec.declare.app import AppRegistry

from .memory_transport import MemoryAgentTransport


@pytest.fixture()
def transport() -> MemoryAgentTransport:
    return MemoryAgentTransport()


@pytest.fixture()
def agent(transport: MemoryAgentTransport) -> BaseAgent:
    """A bare agent with its own registry, so nothing leaks between tests."""
    agent = RekuestAgent(
        name="policy-test", transport=transport, app_registry=AppRegistry()
    )
    # These tests drive process() directly rather than running aconnect(), so install
    # the host by hand exactly as the connect sequence does.
    transport.set_transport_host(agent)
    return agent


async def _run_loop(agent: BaseAgent) -> AsyncIterator[None]:
    async for message in agent.transport.areceive():
        await agent.process(message)
        yield


async def _pump(agent: BaseAgent, count: int) -> None:
    """Process exactly `count` inbound messages."""
    loop = _run_loop(agent)
    for _ in range(count):
        await asyncio.wait_for(loop.__anext__(), timeout=2.0)


def _assign(task: str, interface: str) -> messages.Assign:
    return messages.Assign(
        task=task,
        interface=interface,
        args={"x": 1},
        implementation="impl-1",
        action="action-1",
        reference="ref-1",
        user="user-1",
        org="org-1",
    )


def _errors(transport: MemoryAgentTransport) -> list[str]:
    return [c.error for c in transport.of_type(messages.Critical)]


async def _settle() -> None:
    """Let the watchdog task run to completion."""
    for _ in range(20):
        await asyncio.sleep(0)


# -- the policy objects themselves -------------------------------------------------








# -- the watchdog ------------------------------------------------------------------












@pytest.mark.asyncio
async def test_the_kill_report_is_retained_and_resent_on_reconnect(
    agent: BaseAgent, transport: MemoryAgentTransport
) -> None:
    """A policy kill happens while the socket is down, so the report must survive it.

    Terminal reports are retained until the backend acks them and replayed on the
    next ``Init``. Without that the backend would never learn the task died.
    """
    started = asyncio.Event()

    async def move_stage(x: int) -> int:
        """Stops on disconnect."""
        started.set()
        await asyncio.sleep(60)
        return x

    agent.app_registry.register(
        move_stage,
        policy=CancelOnDisconnect(),
    )
    agent.collect_from_registry()

    transport.feed(_assign("task-1", "move_stage"))
    await _pump(agent, 1)
    await asyncio.wait_for(started.wait(), timeout=2.0)

    await transport.drop_link()
    await _settle()

    assert _errors(transport), "the kill must be reported"
    before = len(transport.of_type(messages.Critical))

    transport.feed(messages.Init(agent="agent-1", inquiries=[]))
    await _pump(agent, 1)

    assert len(transport.of_type(messages.Critical)) > before, (
        "an unacked terminal report must be replayed once the link returns"
    )










# -- the agent-level connection policy ---------------------------------------------




