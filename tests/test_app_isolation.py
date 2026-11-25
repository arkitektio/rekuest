"""Several agents, each bound to its own app, in one process.

Nothing is current: an action, a hook and a structure expansion each get the
clients of the app their agent is bound to, by injection or by binding, and
nothing another agent's app does can reach them.
"""

import asyncio
from typing import Any, TypeVar, cast

import pytest
from arkitekt_runtime import messages
from arkitekt_runtime.agents.base import BaseAgent
from arkitekt_runtime.local import MemoryAgentTransport
from arkitekt_spec.declare.agents.errors import StateRequirementsNotMet
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.client import client
from rath.expansion import ExpandsStructures

from .agent_helpers import run_assignment
from .client_helpers import use

T = TypeVar("T")


class Thing:
    def __init__(self, id: str, owner: str) -> None:
        self.id = id
        self.owner = owner


@client(name="fake", requires={})
class FakeService(ExpandsStructures):
    def __init__(self, owner: str) -> None:
        self.owner = owner

    async def aget_thing(self, id: str) -> Thing:
        return Thing(id, self.owner)

    EXPANDERS = {"@fake/thing": aget_thing}


class FakeApp:
    """What an agent is bound to: it answers get(cls) with its clients."""

    def __init__(self, name: str, *clients: Any) -> None:
        self.name = name
        self.clients = (
            {"fake": FakeService(name)}
            if not clients
            else {type(c).__name__.lower(): c for c in clients}
        )

    def get(self, key: type[T]) -> T | None:
        return next((s for s in self.clients.values() if isinstance(s, key)), None)


async def expand_thing(id: str, service: FakeService) -> Thing:
    return await service.aget_thing(id)


def fake_app_registry() -> AppRegistry:
    """An app using ``FakeService``, and the thing it expands."""
    registry = use(AppRegistry(), FakeService)
    registry.structure_registry.register_as_structure(
        Thing, "@fake/thing", aexpand=cast(Any, expand_thing)
    )
    return registry


def build_agent(name: str) -> BaseAgent:
    app = FakeApp(name)
    registry = fake_app_registry()
    # Bound in place: this registry is this one agent's.
    registry.structure_registry.bind(app)
    return BaseAgent(
        name=name,
        transport=MemoryAgentTransport(),
        app_registry=registry,
        bound_app=app,
    )


def assign(interface: str, **args: Any) -> messages.Assign:
    return messages.Assign(
        task=f"task-{interface}",
        interface=interface,
        args=args,
        implementation="impl-1",
        action="action-1",
        reference="ref-1",
        user="user-1",
        org="org-1",
    )


def thing_ref(id: str) -> dict[str, str]:
    return {"__identifier": "@fake/thing", "object": id}


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_each_agent_expands_and_injects_through_its_own_app() -> None:
    agent_a, agent_b = build_agent("A"), build_agent("B")

    async def probe(thing: Thing, service: FakeService) -> str:
        """Report whose client expanded the thing and whose was injected."""
        return f"expanded={thing.owner}|injected={service.owner}"

    def in_thread(thing: Thing, service: FakeService) -> str:
        """The same, from a worker thread."""
        return f"expanded={thing.owner}|injected={service.owner}"

    for agent in (agent_a, agent_b):
        agent.app_registry.register(probe)
        agent.app_registry.register(in_thread)
        agent.collect_from_registry()

    returns = await asyncio.gather(
        run_assignment(agent_a, assign("probe", thing=thing_ref("1"))),
        run_assignment(agent_b, assign("in_thread", thing=thing_ref("2"))),
    )
    assert returns == [
        {"return0": "expanded=A|injected=A"},
        {"return0": "expanded=B|injected=B"},
    ]


# --------------------------------------------------------------------------- #
# Hooks
# --------------------------------------------------------------------------- #


class UserContext:
    """What a caller passes to ``run(context=...)``: unrelated to the app itself."""

    def __init__(self, label: str) -> None:
        self.label = label


@pytest.mark.asyncio
async def test_a_startup_hook_takes_a_client_and_the_user_context_together() -> None:
    agent = build_agent("A")
    agent.app_registry.app_context(UserContext)
    seen: dict[str, Any] = {}

    @agent.app_registry.startup
    async def boot(user: UserContext, service: FakeService):  # noqa: ANN202
        """Boot."""
        seen.update(user=user.label, injected=service.owner)

    agent.collect_from_registry()
    await agent.arun_startup_hooks(app_context=UserContext("ctx"))

    assert seen == {"user": "ctx", "injected": "A"}


@pytest.mark.asyncio
async def test_a_threaded_startup_hook_gets_its_own_apps_client() -> None:
    agent_a, _ = build_agent("A"), build_agent("B")
    seen: dict[str, Any] = {}

    @agent_a.app_registry.startup
    def boot(service: FakeService):  # noqa: ANN202
        """Boot in a worker thread."""
        seen["injected"] = service.owner

    agent_a.collect_from_registry()
    await agent_a.arun_startup_hooks(app_context=None)

    assert seen == {"injected": "A"}


@pytest.mark.asyncio
async def test_a_background_worker_gets_its_own_apps_client() -> None:
    agent = build_agent("A")
    seen: asyncio.Queue[str] = asyncio.Queue()

    @agent.app_registry.background
    async def watch(service: FakeService) -> None:
        """Background."""
        await seen.put(service.owner)

    agent.collect_from_registry()
    await agent.arun_background()
    assert await asyncio.wait_for(seen.get(), timeout=2) == "A"
    await agent.astop_background()


@pytest.mark.asyncio
async def test_a_shutdown_hook_gets_its_own_apps_client() -> None:
    agent = build_agent("A")
    seen: dict[str, Any] = {}

    @agent.app_registry.shutdown
    async def teardown(service: FakeService) -> None:
        """Teardown."""
        seen["injected"] = service.owner

    agent.collect_from_registry()
    agent._ran_startup_hooks = True
    await agent.arun_shutdown_hooks()

    assert seen == {"injected": "A"}


def test_a_hook_asking_for_a_client_its_app_lacks_names_the_cause() -> None:
    from arkitekt_runtime.agents.hooks.background import WrappedBackgroundTask

    @client(requires={})
    class Other:
        pass

    registry = use(AppRegistry(), Other)

    async def worker(other: Other) -> None:
        """Needs a client the app does not have."""

    with pytest.raises(StateRequirementsNotMet, match="Other"):
        WrappedBackgroundTask(worker, registry.structure_registry).get_kwargs(
            {}, {}, None, bound_app=FakeApp("A")
        )


def test_a_hook_cannot_ask_for_a_task() -> None:
    from arkitekt_runtime.agents.hooks.background import WrappedBackgroundTask
    from arkitekt_runtime.task import Task

    async def worker(task: Task) -> None:
        """A hook runs for no task."""

    with pytest.raises(ValueError, match="runs for no task"):
        WrappedBackgroundTask(worker)
