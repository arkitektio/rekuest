"""A run serves a snapshot of the declaration, never the declaration itself.

The AppRegistry an app registers on is its *declaration*. Each run takes
`snapshot(clients)`: validated, bound to that run's clients, frozen -- as a copy.
So one declaration runs any number of times, concurrently too, and is still
open for registration afterwards.
"""

import asyncio
from dataclasses import dataclass
import inspect
import pickle
from typing import Annotated, Optional

import pytest

from rekuest.agents.base import BaseAgent
from rekuest.state.utils import prepare_injected_variables
from rekuest.app import AppRegistry
from rekuest.errors import RegistryFrozenError
from rekuest.task import Task

from .memory_transport import MemoryAgentTransport
from .agent_helpers import run_assignment
from .service_helpers import with_client
from .test_client_bound_expansion import (
    App,
    Thing,
    ThingClient,
    declared_things,
    expand_thing,
    ref,
    things_registry,
)
from .test_service_client_injection import assign


def declaration() -> AppRegistry:
    registry = declared_things()

    @registry.register
    def owner(thing: Thing) -> str:
        """Who fetched it."""
        return f"{thing.id}@{thing.owner}"

    return registry


def agent_for(snapshot: AppRegistry, app: App, name: str) -> BaseAgent:
    agent = BaseAgent(
        name=name,
        transport=MemoryAgentTransport(),
        app_registry=snapshot,
        bound_app=app,
    )
    agent.collect_from_registry()
    return agent


def test_snapshot_freezes_the_copy_not_the_declaration() -> None:
    registry = declaration()
    snapshot = registry.snapshot()

    def refused(x: int) -> int:
        return x

    with pytest.raises(RegistryFrozenError):
        snapshot.register(refused)

    @registry.register
    def later(x: int) -> int:
        """Registered after a run started."""
        return x

    assert "later" in registry.implementations
    assert "later" not in snapshot.implementations


def test_snapshot_leaves_the_declarations_structures_unbound() -> None:
    registry = declaration()
    registry.snapshot({"things": ThingClient("A")})

    assert registry.structure_registry.get_fullfilled_structure("@things/thing").aexpand is expand_thing


@pytest.mark.asyncio
async def test_two_runs_of_one_declaration_expand_through_their_own_clients() -> None:
    """The actor builder bakes the registry in; the snapshot must re-point it."""
    registry = declaration()
    app_a, app_b = App(ThingClient("A")), App(ThingClient("B"))
    agent_a = agent_for(registry.snapshot(app_a.services), app_a, "a")
    agent_b = agent_for(registry.snapshot(app_b.services), app_b, "b")

    returns = await asyncio.gather(
        run_assignment(agent_a, assign("owner", thing=ref("1"))),
        run_assignment(agent_b, assign("owner", thing=ref("2"))),
    )
    assert returns == [{"return0": "1@A"}, {"return0": "2@B"}]


def test_snapshot_points_state_schemas_at_its_own_structures() -> None:
    registry = AppRegistry()

    @registry.state
    @dataclass
    class Counter:
        value: int = 0

    snapshot = registry.snapshot()
    (schema,) = snapshot.state_registry_schemas.values()
    assert schema is snapshot.structure_registry
    assert schema is not registry.structure_registry


def test_snapshot_keeps_hooks_and_freezes_them() -> None:
    registry = AppRegistry()

    @registry.startup
    def boot():  # noqa: ANN202
        pass

    def other():  # noqa: ANN202
        pass

    snapshot = registry.snapshot()
    assert set(snapshot.hooks_registry.startup_hooks) == {"boot"}
    with pytest.raises(RegistryFrozenError):
        snapshot.startup(other)
    registry.startup(other)


# -- inferring services from an action ------------------------------------- #


class Mikro:
    pass


class Fluss:
    pass


def two_services() -> AppRegistry:
    registry = AppRegistry()
    with_client(registry, Mikro, "mikro")
    with_client(registry, Fluss, "fluss")
    return registry


def test_injected_parameters_are_the_ones_a_declared_service_returns() -> None:
    def segment(
        x: int,
        fluss: Optional[Fluss],
        mikro: Annotated[Mikro, "doc"],
        again: Mikro,
        task: Task,
    ) -> int:
        return x

    injected = prepare_injected_variables(segment, two_services().structure_registry)
    assert injected.service_client_variables == {"fluss": Fluss, "mikro": Mikro, "again": Mikro}
    assert injected.task_variables == ["task"]


def test_plain_and_unresolvable_annotations_are_not_clients() -> None:
    def plain(x: int, y: "NotDefinedAnywhere") -> int:  # noqa: F821
        return x

    injected = prepare_injected_variables(plain, two_services().structure_registry)
    assert injected.service_client_variables == {}


# -- calling an action with no agent ---------------------------------------- #


def test_a_local_task_accepts_reporting_and_attributes_nothing() -> None:
    task = Task.local(id="script")

    task.log("hello")
    task.progress(50, "half")
    task.pausepoint()
    assert task.id == "script" and task.token is None and task.assignment is None


def module_level(x: int, task: Task) -> int:
    """Doubles."""
    task.progress(100)
    return x * 2


REGISTRY = AppRegistry()
module_level = REGISTRY.register(module_level)


def test_a_registered_function_is_still_the_function() -> None:
    assert module_level.__name__ == "module_level"
    assert module_level.__doc__ == "Doubles."
    assert list(inspect.signature(module_level).parameters) == ["x", "task"]
    assert module_level(2, task=Task.local()) == 4
    assert pickle.loads(pickle.dumps(module_level)) is module_level


@pytest.mark.asyncio
async def test_a_snapshot_can_be_bound_after_its_clients_are_built() -> None:
    """The runtime's order: snapshot first (the agent needs it), bind once clients exist."""
    registry = declaration()
    snapshot = registry.snapshot()
    app = App(ThingClient("late"))
    snapshot.structure_registry.bind(app.services)

    agent = agent_for(snapshot, app, "late")
    assert await run_assignment(agent, assign("owner", thing=ref("9"))) == {
        "return0": "9@late"
    }
    assert registry.structure_registry.get_fullfilled_structure("@things/thing").aexpand is expand_thing


def package_with_action() -> AppRegistry:
    """A package registry bringing an action and a state of its own, as fluss brings ``run_flow``.

    Its builder bakes the *package's* structure registry in, which no run ever
    binds a client into.
    """
    package = things_registry()

    @package.register
    def owner(thing: Thing) -> str:
        """Who fetched it."""
        return f"{thing.id}@{thing.owner}"

    @package.state
    @dataclass
    class Counter:
        value: int = 0

    return package


def test_snapshot_repoints_what_was_merged_in_from_a_package() -> None:
    package = package_with_action()
    registry = AppRegistry()
    registry.merge(package, service="things")

    snapshot = registry.snapshot()

    builder = snapshot.actor_builders["owner"]
    assert builder.keywords["structure_registry"] is snapshot.structure_registry
    assert all(
        schema is snapshot.structure_registry
        for schema in snapshot.state_registry_schemas.values()
    )
    # Merging reads the package; its own builder still points at the package.
    assert (
        package.actor_builders["owner"].keywords["structure_registry"]
        is package.structure_registry
    )


@pytest.mark.asyncio
async def test_a_packages_action_expands_through_each_runs_own_client() -> None:
    package = package_with_action()
    registry = AppRegistry()
    registry.merge(package, service="things")
    app_a, app_b = App(ThingClient("A")), App(ThingClient("B"))
    agent_a = agent_for(registry.snapshot(app_a.services), app_a, "a")
    agent_b = agent_for(registry.snapshot(app_b.services), app_b, "b")

    returns = await asyncio.gather(
        run_assignment(agent_a, assign("owner", thing=ref("1"))),
        run_assignment(agent_b, assign("owner", thing=ref("2"))),
    )
    assert returns == [{"return0": "1@A"}, {"return0": "2@B"}]
