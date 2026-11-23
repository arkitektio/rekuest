"""Driving an agent through one assignment, for tests that exercise actors end to end."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from arkitekt_runtime import messages
from arkitekt_runtime.agents.base import BaseAgent
from arkitekt_runtime.local import MemoryAgentTransport


async def until_sent(
    transport: MemoryAgentTransport,
    *kinds: type[messages.FromAgentMessage],
    timeout: float = 5.0,
) -> None:
    """Return once the agent has sent a message of one of ``kinds``.

    Woken by each message the agent sends (the transport's watch queue), not by
    polling.
    """
    watcher = transport.watch()

    async def until() -> None:
        while not any(transport.of_type(kind) for kind in kinds):
            await watcher.get()

    try:
        await asyncio.wait_for(until(), timeout=timeout)
    finally:
        transport.unwatch(watcher)


async def _run_loop(agent: BaseAgent) -> AsyncIterator[None]:
    async for message in agent.transport.areceive():
        await agent.process(message)
        yield


async def run_assignment(agent: BaseAgent, message: messages.Assign) -> dict[str, Any]:
    """Feed one assignment and return what the actor yielded."""
    transport: MemoryAgentTransport = agent.transport  # type: ignore[assignment]
    transport.feed(message)
    loop = _run_loop(agent)
    await asyncio.wait_for(loop.__anext__(), timeout=2.0)

    await until_sent(transport, messages.Yield, messages.Failed, messages.Critical)
    failures = transport.of_type(messages.Failed) + transport.of_type(messages.Critical)
    assert not failures, f"the assignment failed: {failures}"
    returns = transport.of_type(messages.Yield)[0].returns
    assert returns is not None
    return returns
