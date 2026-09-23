"""An agent registers by connecting: ``Register`` carries its declaration, ``Init`` answers.

Nothing in the agent's path calls the rekuest GraphQL API. These drive a ``RekuestAgent``
over the recording transport: what goes out in the handshake, what ``Init`` establishes,
how a refusal fails ``aconnect``, and the shelve round trips an agent makes after it.
"""

import asyncio
import warnings

import pytest

from rekuest import messages
from rekuest.agents.backend import SocketAgentBackend
from rekuest.agents.base import BaseAgent, RekuestAgent
from rekuest.agents.errors import AgentException
from rekuest.agents.transport.types import HandshakeParams
from rekuest.app import AppRegistry
from rekuest.catalogs import CatalogWarning
from rekuest.scalars import Identifier

from .memory_transport import MemoryAgentTransport


class RecordingTransport(MemoryAgentTransport):
    """A memory transport that asks its host for the handshake, as the websocket one does."""

    handshakes: list[HandshakeParams] = []

    def model_post_init(self, __context: object) -> None:
        super().model_post_init(__context)
        self.handshakes = []

    async def aconnect(self) -> None:
        assert self._host is not None, "the agent installs itself before connecting"
        self.handshakes.append(await self._host.aget_handshake_params())
        await super().aconnect()


async def _until(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0)


def _agent(
    transport: MemoryAgentTransport, registry: AppRegistry | None = None
) -> RekuestAgent:
    return RekuestAgent(
        transport=transport,
        app_registry=registry if registry is not None else AppRegistry(),
        name="reg-test",
    )


async def _connected(
    transport: RecordingTransport,
    registry: AppRegistry | None = None,
    **init,
) -> RekuestAgent:
    """An agent through ``aconnect``: the backend acknowledged what ``Register`` declared."""
    agent = _agent(transport, registry)
    connecting = asyncio.create_task(agent.aconnect(timeout=2.0))
    await _until(lambda: transport.handshakes)
    transport.feed(messages.Init(agent="agent-1", hash=transport.handshakes[0].declaration.hash, **init))
    await connecting
    return agent


@pytest.mark.asyncio
async def test_register_carries_the_declaration_and_init_registers_the_agent() -> None:
    transport = RecordingTransport()
    agent = await _connected(transport)

    (handshake,) = transport.handshakes
    assert handshake.session_id == agent.current_session
    declaration = handshake.declaration
    assert declaration is not None
    assert declaration.hash == await agent.aget_hash()
    assert declaration.name == "reg-test" and declaration.implementations == []

    assert isinstance(agent.backend, SocketAgentBackend)
    assert agent.registered_agent_id == "agent-1"
    (session_init,) = transport.of_type(messages.SessionInit)
    assert session_init.session_id == agent.current_session
    await agent.atear_down()


@pytest.mark.asyncio
async def test_the_declaration_is_spelled_the_way_the_socket_models_are() -> None:
    """snake_case, not the generated models' GraphQL aliases.

    The socket is not GraphQL. The backend validates ``Register`` against its own
    pydantic models (``rekuest_core.inputs.models``), which are spelled in
    snake_case and forbid extras -- so a dump carrying ``portGroups`` is refused
    field by field, and the agent never registers. Only a live backend catches
    that, which is why the spelling is pinned here.
    """
    registry = AppRegistry()

    @registry.register
    def crop(x: int) -> int:
        """Takes a number."""
        return x

    transport = RecordingTransport()
    agent = await _connected(transport, registry=registry)

    (handshake,) = transport.handshakes
    assert handshake.declaration is not None
    (implementation,) = handshake.declaration.implementations
    definition = implementation["definition"]

    aliased = [key for key in definition if key != key.lower()]
    assert not aliased, f"{aliased} are GraphQL aliases; the socket wants snake_case"
    assert "port_groups" in definition or "portGroups" not in definition

    await agent.atear_down()


@pytest.mark.asyncio
async def test_init_diagnostics_surface_as_catalog_warnings() -> None:
    transport = RecordingTransport()
    finding = messages.RegistrationDiagnostic(code="unknown_operation", message="'nope' is not provided", path="scan.args.x")

    with pytest.warns(CatalogWarning, match="unknown_operation: 'nope' is not provided \\(at scan.args.x\\)"):
        agent = await _connected(transport, diagnostics=[finding])
    await agent.atear_down()


@pytest.mark.asyncio
async def test_a_refused_registration_fails_connect_and_tears_down() -> None:
    transport = RecordingTransport()
    agent = _agent(transport)
    connecting = asyncio.create_task(agent.aconnect(timeout=2.0))
    await _until(lambda: transport.handshakes)

    transport.feed(messages.ProtocolError(error="Registration refused: does not fit the catalog"))

    with pytest.raises(AgentException, match="refused this agent's registration.*does not fit"):
        await connecting
    assert not transport.connected
    assert agent.registered_agent_id is None


@pytest.mark.asyncio
async def test_a_stream_that_ends_before_init_fails_connect() -> None:
    transport = RecordingTransport()
    agent = _agent(transport)
    connecting = asyncio.create_task(agent.aconnect(timeout=2.0))
    await _until(lambda: transport.handshakes)

    transport.close_stream()

    with pytest.raises(AgentException, match="closed before"):
        await connecting


@pytest.mark.asyncio
async def test_put_on_shelve_round_trips_over_the_socket() -> None:
    transport = RecordingTransport()
    agent = await _connected(transport)
    value = object()

    shelving = asyncio.create_task(agent.aput_on_shelve(Identifier.validate("@test/thing"), value))
    await _until(lambda: transport.of_type(messages.Shelve))
    (sent,) = transport.of_type(messages.Shelve)
    assert sent.identifier == "@test/thing" and sent.label == str(value)

    transport.feed(messages.Shelved(ref=sent.ref, drawer="drawer-1"))
    assert await shelving == "drawer-1"
    assert agent.shelve["drawer-1"] is value
    await agent.atear_down()


@pytest.mark.asyncio
async def test_collect_drops_the_drawer_and_sends_unshelve() -> None:
    transport = RecordingTransport()
    agent = await _connected(transport)
    agent.shelve["drawer-1"] = object()
    agent.shelve["drawer-2"] = object()

    transport.feed(messages.Collect(drawers=["drawer-1"]))
    await _until(lambda: transport.of_type(messages.Unshelve))
    (sent,) = transport.of_type(messages.Unshelve)
    assert sent.drawer == "drawer-1" and "drawer-1" not in agent.shelve

    # An error on the answer is logged; the loop keeps serving.
    transport.feed(messages.Unshelved(ref=sent.ref, error="unknown drawer"))
    transport.feed(messages.Collect(drawers=["drawer-2"]))
    await _until(lambda: len(transport.of_type(messages.Unshelve)) == 2)
    assert "drawer-2" not in agent.shelve
    await agent.atear_down()


@pytest.mark.asyncio
async def test_collect_of_an_unknown_drawer_keeps_the_loop_alive() -> None:
    transport = RecordingTransport()
    agent = await _connected(transport)
    agent.shelve["drawer-1"] = object()

    # A drawer this agent never held (or already dropped) must not kill the loop.
    transport.feed(messages.Collect(drawers=["unknown"]))
    transport.feed(messages.Collect(drawers=["drawer-1"]))
    await _until(lambda: "drawer-1" not in agent.shelve)
    await agent.atear_down()


@pytest.mark.asyncio
async def test_teardown_empties_the_shelve() -> None:
    transport = RecordingTransport()
    agent = await _connected(transport)
    agent.shelve["drawer-1"] = object()

    await agent.atear_down()

    assert agent.shelve == {}


@pytest.mark.asyncio
async def test_teardown_empties_the_shelve_even_when_it_fails() -> None:
    transport = RecordingTransport()
    agent = await _connected(transport)
    agent.shelve["drawer-1"] = object()

    async def failing_disconnect() -> None:
        raise RuntimeError("transport broke while disconnecting")

    object.__setattr__(transport, "adisconnect", failing_disconnect)

    with pytest.raises(RuntimeError):
        await agent.atear_down()

    assert agent.shelve == {}


@pytest.mark.asyncio
async def test_missing_drawer_is_a_clear_error() -> None:
    transport = RecordingTransport()
    agent = await _connected(transport)

    with pytest.raises(AgentException, match="drawer-x"):
        await agent.aget_from_shelve("drawer-x")
    await agent.atear_down()


@pytest.mark.asyncio
async def test_work_assigned_before_activation_is_held_and_replayed() -> None:
    transport = MemoryAgentTransport()
    agent = _agent(transport)
    assign = messages.Assign(task="task-1", interface="nope", args={}, user="u", org="o", action="a", implementation="i")

    agent._provider_ready = False
    await agent.process(assign)
    assert transport.of_type(messages.Critical) == []

    await agent._arelease_held_provider_messages()
    assert agent._provider_ready
    (critical,) = transport.of_type(messages.Critical)
    assert critical.task == "task-1"


@pytest.mark.asyncio
async def test_a_base_agent_declares_too_but_keeps_its_local_backend() -> None:
    transport = RecordingTransport()
    agent = BaseAgent(transport=transport, app_registry=AppRegistry(), name="local")
    connecting = asyncio.create_task(agent.aconnect(timeout=2.0))
    await _until(lambda: transport.handshakes)
    assert transport.handshakes[0].declaration is not None

    transport.feed(messages.Init(agent="agent-2"))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        await connecting
    assert agent.registered_agent_id is None
    await agent.atear_down()


@pytest.mark.asyncio
async def test_register_carries_the_agents_description() -> None:
    """A described agent says so in ``Register``; an undescribed one stays silent.

    The two halves matter for different reasons. The description has to reach the
    backend for it to be shown beside the name, and an agent that declares none
    has to leave the field *unset* rather than send an explicit null -- the
    server's contract is that omitting it keeps whatever the agent already has,
    so a null would be the one way to wipe a description the UI had.
    """
    transport = RecordingTransport()
    agent = RekuestAgent(
        transport=transport,
        app_registry=AppRegistry(),
        name="reg-test",
        description="What this agent is, in a sentence.",
    )
    connecting = asyncio.create_task(agent.aconnect(timeout=2.0))
    await _until(lambda: transport.handshakes)
    transport.feed(
        messages.Init(agent="agent-1", hash=transport.handshakes[0].declaration.hash)
    )
    await connecting

    (handshake,) = transport.handshakes
    declaration = handshake.declaration
    assert declaration is not None
    assert declaration.description == "What this agent is, in a sentence."
    await agent.atear_down()

    # The undescribed agent: the key is absent, not null.
    plain = RecordingTransport()
    await _connected(plain)
    (plain_handshake,) = plain.handshakes
    declared = plain_handshake.declaration
    assert declared is not None
    assert declared.description is None
    assert "description" not in declared.model_dump(exclude_unset=True)


def test_a_description_changes_the_definition_hash() -> None:
    """Otherwise the backend would skip reconciling and never pick a new one up.

    The backend stores whatever hash ``Register`` carries and skips the whole
    reconciliation when the next one matches. So a description that did not move
    the hash would be silently dropped on every agent that had already
    registered -- while an agent that declares none must keep the hash it had
    before descriptions existed, or every such agent re-reconciles once for
    nothing.
    """
    registry = AppRegistry()

    def canonical(**kwargs: object) -> str:
        return registry.to_implement_agent_input(name="a:1", **kwargs).model_dump_json(
            by_alias=True, exclude_none=True
        )

    assert canonical() == canonical(description=None)
    assert canonical(description="One thing.") != canonical()
    assert canonical(description="One thing.") != canonical(description="Another thing.")


@pytest.mark.asyncio
async def test_a_stream_that_ends_tears_the_agent_down() -> None:
    transport = RecordingTransport()
    agent = await _connected(transport)
    agent.shelve["drawer-1"] = object()

    looping = asyncio.create_task(agent.aloop())
    await _until(lambda: agent.running)
    transport.close_stream()
    await asyncio.wait_for(looping, timeout=2.0)

    assert not agent.running
    assert not transport.connected
    assert agent.shelve == {}


@pytest.mark.asyncio
async def test_a_failing_stream_stops_the_agent_running() -> None:
    transport = RecordingTransport()
    agent = await _connected(transport)

    looping = asyncio.create_task(agent.aloop())
    await _until(lambda: agent.running)
    transport.fail(RuntimeError("socket died"))
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(looping, timeout=2.0)

    assert not agent.running
