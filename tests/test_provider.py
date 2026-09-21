"""A provider is declared beside a service and builds the app's agent for a run.

It is injected like a service builder -- requirements, tokens, fakts, the run's
registry -- plus the clients the run built, by annotation. A run builds it after
every client and owns the agent; the provider binds and starts nothing.
"""

from typing import Annotated, Any

import pytest
from fakts import Alias, Fakts, Require
from fakts.testing import build_testing_fakts

from rekuest.app import AppRegistry
from rekuest.errors import RegistryFrozenError
from rekuest.provider import ProviderBuildError, ProviderDefinitionError
from rekuest.service import ServiceDefinitionError


class Client:
    def __init__(self, where: str = "") -> None:
        self.where = where


class Agent:
    """Enough of an agent to be one: the lifecycle the run drives."""

    force: bool | None = None

    def __init__(self, client: Client, registry: AppRegistry) -> None:
        self.client = client
        self.registry = registry

    async def aprovide(self, context: Any) -> None: ...  # noqa: ANN401
    async def aconnect(self, context: Any = None, timeout: float | None = None) -> None: ...  # noqa: ANN401
    async def aloop(self) -> None: ...


def package() -> AppRegistry:
    registry = AppRegistry()

    @registry.service()
    def thing(thing: Annotated[Alias, Require("live.test.thing")]) -> Client:
        """The thing's client."""
        return Client(str(thing))

    return registry


def test_a_provider_reads_its_injections_and_requirements() -> None:
    registry = package()

    @registry.provider()
    def thing_agent(
        thing: Annotated[Alias, Require("live.test.thing")],
        fakts: Fakts,
        registry: AppRegistry,
        client: Client,
    ) -> Agent:
        """Serves the thing."""
        return Agent(client, registry)

    assert registry.providers == {"thing_agent": thing_agent}
    assert registry.provider_declaration is thing_agent
    assert [r.key for r in thing_agent.get_requirements()] == ["thing"]
    assert thing_agent.returns is Agent
    assert thing_agent.needs_fakts


def test_a_provider_taking_only_the_registry_and_clients_needs_no_fakts() -> None:
    registry = package()

    @registry.provider()
    def local_agent(registry: AppRegistry, client: Client) -> Agent:
        """Serves locally."""
        return Agent(client, registry)

    assert not local_agent.needs_fakts


def test_a_client_is_injectable_only_once_its_service_is_declared() -> None:
    with pytest.raises(ProviderDefinitionError, match="cannot supply"):

        @AppRegistry().provider()
        def early(client: Client) -> Agent:
            """Asks before anyone returns a Client."""
            return Agent(client, AppRegistry())


def test_a_provider_must_say_what_it_returns() -> None:
    with pytest.raises(ProviderDefinitionError, match="returns"):

        @package().provider()
        def unknown(registry: AppRegistry):  # noqa: ANN202
            """No return annotation."""
            return None


@pytest.mark.asyncio
async def test_build_hands_over_the_built_client_and_the_snapshot() -> None:
    registry = package()

    @registry.provider()
    def thing_agent(registry: AppRegistry, client: Client) -> Agent:
        """Serves the thing."""
        return Agent(client, registry)

    snapshot = registry.snapshot()
    client = Client("built")
    agent = await thing_agent.build(None, snapshot, {"thing": client})

    assert agent.client is client and agent.registry is snapshot


@pytest.mark.asyncio
async def test_build_names_the_missing_client_and_the_missing_fakts() -> None:
    registry = package()

    @registry.provider()
    def thing_agent(thing: Annotated[Alias, Require("live.test.thing")], client: Client) -> Agent:
        """Serves the thing."""
        return Agent(client, AppRegistry())

    async with build_testing_fakts(aliases={"thing": "http://t"}) as fakts:
        with pytest.raises(ProviderBuildError, match="Client"):
            await thing_agent.build(fakts, registry, {})
    with pytest.raises(ProviderBuildError, match="fakts"):
        await thing_agent.build(None, registry, {"thing": Client()})


@pytest.mark.asyncio
async def test_a_provider_that_returns_no_agent_is_refused_at_build() -> None:
    registry = package()

    @registry.provider()
    def not_an_agent(registry: AppRegistry) -> Client:
        """Returns a client instead."""
        return Client()

    with pytest.raises(ProviderBuildError, match="not an agent"):
        await not_an_agent.build(None, registry, {})


def test_an_app_takes_a_provider_in_with_its_service_and_structures() -> None:
    pkg = package()

    @pkg.provider()
    def thing_agent(registry: AppRegistry, client: Client) -> Agent:
        """Serves the thing."""
        return Agent(client, registry)

    app = AppRegistry()
    app.register_provider(thing_agent)

    assert app.providers == {"thing_agent": thing_agent}
    assert "thing" in app.services
    assert app.structure_registry.is_client(Client)
    app.register_provider(thing_agent)  # again: nothing happens


def test_an_app_is_served_by_one_agent() -> None:
    a, b = package(), AppRegistry()

    @a.provider()
    def one(registry: AppRegistry) -> Agent:
        """One."""
        return Agent(Client(), registry)

    @b.provider()
    def two(registry: AppRegistry) -> Agent:
        """Two."""
        return Agent(Client(), registry)

    with pytest.raises(ValueError, match="one agent"):
        a.provider()(two._function)  # type: ignore[attr-defined]
    app = AppRegistry()
    app.register_provider(one)
    with pytest.raises(ValueError, match="one agent"):
        app.register_provider(two)


def test_a_snapshot_carries_the_provider_and_refuses_another() -> None:
    registry = package()

    @registry.provider()
    def thing_agent(registry: AppRegistry) -> Agent:
        """Serves."""
        return Agent(Client(), registry)

    snapshot = registry.snapshot()
    assert snapshot.provider_declaration is thing_agent
    with pytest.raises(RegistryFrozenError):

        @snapshot.provider()
        def later(registry: AppRegistry) -> Agent:
            """Too late."""
            return Agent(Client(), registry)


def test_a_service_says_whether_it_needs_fakts() -> None:
    registry = AppRegistry()

    @registry.service()
    def offline(registry: AppRegistry) -> Client:
        """Needs nothing resolved."""
        return Client()

    assert not offline.needs_fakts
    assert package().services["thing"].needs_fakts


@pytest.mark.asyncio
async def test_a_service_needing_fakts_refuses_to_build_without() -> None:
    with pytest.raises(RuntimeError, match="fakts"):
        await package().services["thing"].build(None, AppRegistry())


def test_a_service_still_cannot_ask_for_another_client() -> None:
    registry = package()
    with pytest.raises(ServiceDefinitionError):

        @registry.service()
        def dependent(client: Client) -> Client:
            """A service never takes another client."""
            return client
