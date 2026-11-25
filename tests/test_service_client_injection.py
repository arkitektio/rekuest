"""A parameter annotated with a client the app builds receives that client.

`def segment(x: int, mikro: Mikro)`: the app uses the `Mikro` client, so the agent
fills `mikro` with its bound app's `Mikro` — the one shared instance, not a per-task
copy. What makes its requests attributable is the ambient task, which the actor
sets around the body.
"""

import asyncio
from typing import Any, TypeVar

import pytest
from arkitekt_runtime import messages
from arkitekt_runtime.agents.base import BaseAgent
from arkitekt_runtime.local import MemoryAgentTransport
from arkitekt_spec.declare.agents.errors import StateRequirementsNotMet
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.client import client
from rath.task import current_task, token_of

from rekuest.agents.agent import RekuestAgent

from .agent_helpers import run_assignment
from .client_helpers import use

T = TypeVar("T")


@client(name="fake", requires={})
class FakeClient:
    """Stands in for a generated client class such as Mikro."""

    def __init__(self, owner: str, token: str | None = None) -> None:
        self.owner = owner
        self.token = token

    def seen_token(self) -> str | None:
        """What this call would be attributed to, as `_headers` decides it."""
        return token_of(None)


@client(name="other", requires={})
class OtherClient:
    pass


class FakeApp:
    __app_context__ = True

    def __init__(self, name: str, *clients: Any) -> None:
        self.name = name
        self.clients = {type(c).__name__: c for c in clients}

    def get(self, key: type[T]) -> T | None:
        return next((c for c in self.clients.values() if isinstance(c, key)), None)


def registry_with_clients() -> AppRegistry:
    """A registry using both fake clients."""
    return use(AppRegistry(), FakeClient, OtherClient)


def build_agent(app: FakeApp | None) -> BaseAgent:
    return RekuestAgent(
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


@pytest.mark.asyncio
async def test_async_and_threaded_functions_receive_their_apps_client() -> None:
    agent_a = build_agent(FakeApp("A", FakeClient("A")))
    agent_b = build_agent(FakeApp("B", FakeClient("B")))

    async def in_loop(x: int, client: FakeClient) -> str:
        """Async."""
        return f"{client.owner}{x}"

    def in_thread(x: int, client: FakeClient | None) -> str:
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
async def test_each_task_attributes_its_own_calls() -> None:
    """Three agents on one app share its one client; each task attributes its own."""
    app = FakeApp("A", FakeClient("A"))
    agents = [build_agent(app) for _ in range(3)]

    async def who(client: FakeClient) -> str:
        """Reports what a call through its client would be attributed to."""
        await asyncio.sleep(0.01)
        return str(client.seen_token())

    for agent in agents:
        agent.app_registry.register(who)
        agent.collect_from_registry()

    results = await asyncio.gather(
        run_assignment(agents[0], assign("who", token="t1")),
        run_assignment(agents[1], assign("who", token="t2")),
        run_assignment(agents[2], assign("who")),
    )
    assert results == [{"return0": "t1"}, {"return0": "t2"}, {"return0": "None"}]
    shared = app.get(FakeClient)
    assert shared is not None
    assert shared.token is None, "the shared client is never mutated"
    assert current_task.get() is None, "no task outlives its assignment"


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
