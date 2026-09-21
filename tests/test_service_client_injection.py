"""A parameter annotated with a client the app builds receives that client.

`def segment(x: int, mikro: Mikro)`: `mikro` is not a port, because a service
declared on the registry returns a `Mikro`; the agent fills it with its bound
app's `Mikro` — as a per-task view when the task carries a provenance token,
since one client is shared by every concurrent task.
"""

import asyncio
from typing import Annotated, Any, Optional, Protocol

import pytest

from rekuest import messages
from rekuest.agents.base import BaseAgent
from rekuest.agents.errors import StateRequirementsNotMet
from rekuest.api.schema import PortKind
from rekuest.app import AppRegistry
from rekuest.definition.define import prepare_definition
from rekuest.structures.registry import StructureRegistry

from .memory_transport import MemoryAgentTransport
from .agent_helpers import run_assignment
from .service_helpers import with_client


class FakeClient:
    """Stands in for a generated client class such as Mikro."""

    def __init__(self, owner: str, token: str | None = None) -> None:
        self.owner = owner
        self.token = token

    def for_task(self, task: Any) -> "FakeClient":  # noqa: ANN401
        return FakeClient(self.owner, task.token)


class OtherClient:
    pass


class Lab(Protocol):
    """A dependency protocol: not ``runtime_checkable``, so not an ``issubclass`` target."""

    def measure(self, x: int) -> int:
        """Measure."""
        ...


class FakeApp:
    __app_context__ = True

    def __init__(self, name: str, *clients: Any) -> None:
        self.name = name
        self.clients = {type(c).__name__: c for c in clients}
        self.services = self.clients

    def get(self, key: type) -> Any:
        return next((s for s in self.services.values() if isinstance(s, key)), None)


def registry_with_clients() -> AppRegistry:
    """A registry on which services return both fake clients, so they are clients."""
    registry = AppRegistry()
    with_client(registry, FakeClient, "fake")
    with_client(registry, OtherClient, "other")
    return registry


def build_agent(app: FakeApp | None) -> BaseAgent:
    return BaseAgent(
        name=app.name if app else "standalone",
        transport=MemoryAgentTransport(),
        app_registry=registry_with_clients(),
        bound_app=app,
    )


def assign(interface: str, token: str | None = None, **args: Any) -> messages.Assign:
    return messages.Assign(
        task=f"task-{interface}-{token}",
        interface=interface,
        args=args,
        implementation="impl-1",
        action="action-1",
        reference="ref-1",
        user="user-1",
        org="org-1",
        token=token,
    )


def test_a_client_is_what_a_declared_service_returns_seen_through_optional_and_annotated() -> None:
    structures = registry_with_clients().structure_registry
    assert structures.is_client(FakeClient)
    assert structures.is_client(Optional[FakeClient])
    assert structures.is_client(FakeClient | None)
    assert structures.is_client(Annotated[FakeClient, "doc"])
    assert not structures.is_client(FakeClient | OtherClient)
    assert not structures.is_client(FakeClient("x"))
    assert not structures.is_client(int)
    assert not structures.is_client(Lab), "a declared protocol is a dependency, not a client"
    assert not StructureRegistry().is_client(FakeClient), "no service, no client"


@pytest.mark.parametrize(
    "annotation", [FakeClient, Optional[FakeClient], Annotated[FakeClient, "doc"]]
)
def test_client_parameter_is_not_a_port(annotation: Any) -> None:
    def takes_client(x: int, client) -> int:  # noqa: ANN001
        """Takes a client."""
        return x

    takes_client.__annotations__["client"] = annotation
    definition = prepare_definition(
        takes_client, structure_registry=registry_with_clients().structure_registry
    )

    assert [port.key for port in definition.args] == ["x"]
    assert definition.args[0].kind == PortKind.INT


@pytest.mark.asyncio
async def test_async_and_threaded_functions_receive_their_apps_client() -> None:
    agent_a = build_agent(FakeApp("A", FakeClient("A")))
    agent_b = build_agent(FakeApp("B", FakeClient("B")))

    async def in_loop(x: int, client: FakeClient) -> str:
        """Async."""
        return f"{client.owner}{x}"

    def in_thread(x: int, client: Optional[FakeClient]) -> str:
        """Sync, runs in a worker thread."""
        assert client is not None
        return f"{client.owner}{x}"

    for agent in (agent_a, agent_b):
        agent.app_registry.register(in_loop)
        agent.app_registry.register(in_thread)
        agent.collect_from_registry()

    # run_assignment reads the agent's first Yield, so one assignment per agent at once.
    results = await asyncio.gather(
        run_assignment(agent_a, assign("in_loop", x=1)),
        run_assignment(agent_b, assign("in_thread", x=2)),
    )
    assert results == [{"return0": "A1"}, {"return0": "B2"}]


@pytest.mark.asyncio
async def test_each_task_gets_a_view_carrying_its_own_token() -> None:
    """Three agents on one app share its one client; each task's view is its own."""
    app = FakeApp("A", FakeClient("A"))
    agents = [build_agent(app) for _ in range(3)]

    async def who(client: FakeClient) -> str:
        """Reports the token its client carries."""
        await asyncio.sleep(0.01)
        return str(client.token)

    for agent in agents:
        agent.app_registry.register(who)
        agent.collect_from_registry()

    results = await asyncio.gather(
        run_assignment(agents[0], assign("who", token="t1")),
        run_assignment(agents[1], assign("who", token="t2")),
        run_assignment(agents[2], assign("who")),
    )
    assert results == [{"return0": "t1"}, {"return0": "t2"}, {"return0": "None"}]
    assert app.get(FakeClient).token is None, "the shared client is never mutated"


@pytest.mark.asyncio
async def test_a_client_the_app_lacks_fails_naming_it() -> None:
    agent = build_agent(FakeApp("A", FakeClient("A")))

    async def needs_other(client: OtherClient) -> str:
        """Needs a client the app was built without."""
        return "unreachable"

    agent.app_registry.register(needs_other)
    agent.collect_from_registry()
    actor = await agent.aspawn_actor_from_assign(assign("needs_other"))

    with pytest.raises(StateRequirementsNotMet, match="OtherClient"):
        await actor.aget_injected_locals()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_asking_for_a_client_without_an_app_fails_at_the_cause() -> None:
    agent = build_agent(None)

    async def needs_client(client: FakeClient) -> str:
        """Needs a client."""
        return "unreachable"

    agent.app_registry.register(needs_client)
    agent.collect_from_registry()
    actor = await agent.aspawn_actor_from_assign(assign("needs_client"))

    with pytest.raises(StateRequirementsNotMet, match="not bound to an app"):
        await actor.aget_injected_locals()  # type: ignore[attr-defined]
