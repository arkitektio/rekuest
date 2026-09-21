"""The socket control plane: what ``Init`` told us, and the shelve round trips.

Registration itself is the transport handshake; what the control plane owns is the
bookkeeping ``Init`` carries and the ``Shelve``/``Unshelve`` pairs, driven here over a
recording sink.
"""

import asyncio
import logging

import pytest

from rekuest import messages
from rekuest.agents.control import SocketControlPlane
from rekuest.agents.errors import AgentException
from rekuest.scalars import Identifier

from .memory_transport import MemoryAgentTransport


async def _until(predicate, timeout: float = 1.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0)


def test_handle_init_records_the_agent_and_its_hash() -> None:
    plane = SocketControlPlane(MemoryAgentTransport())
    assert not plane.acknowledged and plane.agent_id is None

    plane.handle_init(messages.Init(agent="agent-1", hash="h1"))

    assert plane.acknowledged
    assert plane.agent_id == "agent-1" and plane.server_hash == "h1"


@pytest.mark.asyncio
async def test_ashelve_sends_shelve_and_resolves_on_shelved() -> None:
    sink = MemoryAgentTransport()
    plane = SocketControlPlane(sink)

    shelving = asyncio.create_task(
        plane.ashelve(Identifier.validate("@test/thing"), "res-1", label="a thing")
    )
    await _until(lambda: sink.of_type(messages.Shelve))
    (sent,) = sink.of_type(messages.Shelve)
    assert sent.identifier == "@test/thing" and sent.resource_id == "res-1"
    assert sent.label == "a thing"

    plane.handle_shelved(messages.Shelved(ref=sent.ref, drawer="drawer-1"))
    assert await shelving == "drawer-1"


@pytest.mark.asyncio
async def test_a_shelved_error_raises() -> None:
    sink = MemoryAgentTransport()
    plane = SocketControlPlane(sink)

    shelving = asyncio.create_task(plane.ashelve(Identifier.validate("@test/thing"), "res-2"))
    await _until(lambda: sink.of_type(messages.Shelve))
    (sent,) = sink.of_type(messages.Shelve)

    plane.handle_shelved(messages.Shelved(ref=sent.ref, error="no shelve"))
    with pytest.raises(AgentException, match="no shelve"):
        await shelving


@pytest.mark.asyncio
async def test_a_lost_reply_times_out_instead_of_hanging() -> None:
    plane = SocketControlPlane(MemoryAgentTransport(), reply_timeout=0.01)

    with pytest.raises(AgentException, match="did not answer"):
        await plane.ashelve(Identifier.validate("@test/thing"), "res-3")


@pytest.mark.asyncio
async def test_aunshelve_is_send_only_and_an_error_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    sink = MemoryAgentTransport()
    plane = SocketControlPlane(sink)

    await plane.aunshelve("drawer-1")
    (sent,) = sink.of_type(messages.Unshelve)
    assert sent.drawer == "drawer-1"

    with caplog.at_level(logging.WARNING):
        plane.handle_unshelved(messages.Unshelved(ref=sent.ref, error="unknown drawer"))
    assert "unknown drawer" in caplog.text


@pytest.mark.asyncio
async def test_fail_pending_rejects_every_waiter() -> None:
    sink = MemoryAgentTransport()
    plane = SocketControlPlane(sink)
    first = asyncio.create_task(plane.ashelve(Identifier.validate("@test/thing"), "r-1"))
    second = asyncio.create_task(plane.ashelve(Identifier.validate("@test/thing"), "r-2"))
    await _until(lambda: len(sink.of_type(messages.Shelve)) == 2)

    plane.fail_pending(AgentException("torn down"))

    for waiter in (first, second):
        with pytest.raises(AgentException, match="torn down"):
            await waiter
