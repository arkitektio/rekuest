"""A declared structure expands through the client its registry was bound to --
once, at bind time, by service name, never looked up per call. The client names
its expanders by identifier (rath's ``ExpandsStructures``).

This replaced resolving the client from an ambient context. It deliberately
threads nothing through the serializer: the 09-17 design that did broke because
the recursive `_expand` helpers dropped the app, which nested ports exercise.
"""

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest
from rath.expansion import ExpandsStructures

from rekuest.agents.base import BaseAgent
from rekuest.app import AppRegistry
from rekuest.structures.errors import StructureClientError
from rekuest.structures.registry import StructureRegistry

from .memory_transport import MemoryAgentTransport
from .test_service_client_injection import assign
from .agent_helpers import run_assignment
from .service_helpers import with_client


class Thing:
    def __init__(self, id: str, owner: str) -> None:
        self.id = id
        self.owner = owner


class ThingClient(ExpandsStructures):
    """Its expanders are plain methods, as generated client methods are."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.batches: list[list[str]] = []

    async def aget_thing(self, id: str) -> Thing:
        return Thing(id, self.owner)

    async def aget_things(self, ids: Sequence[str]) -> list[Thing]:
        self.batches.append(list(ids))
        return [Thing(id, self.owner) for id in ids]

    EXPANDERS = {"@things/thing": aget_thing}


class BatchingThingClient(ThingClient):
    """Fetches several things in one request, as a federated client does."""

    async def aexpand_many(self, identifier: str, ids: Sequence[str]) -> list[Thing]:
        assert identifier == "@things/thing"
        return await self.aget_things(ids)


async def expand_thing(id: str, things: ThingClient) -> Thing:
    """The expander: it names the client it needs, and the client fetches."""
    return await things.aget_thing(id)


def things_registry() -> AppRegistry:
    """What the things package brings: a service returning its client, and the thing."""
    registry = AppRegistry()
    with_client(registry, ThingClient, "things")
    registry.structure("@things/thing")(expand_thing)
    return registry


def declared_things() -> AppRegistry:
    """A registry that took the things package in, so its thing is stamped ``things``."""
    registry = AppRegistry()
    registry.merge(things_registry(), service="things")
    return registry


def ref(id: str) -> dict[str, str]:
    return {"__identifier": "@things/thing", "object": id}


class App:
    __app_context__ = True

    def __init__(self, *clients: Any) -> None:
        # Keyed by service name: what a registry is bound with.
        self.clients = {"things": c for c in clients}
        self.services = self.clients

    def get(self, key: type) -> Any:
        return next((c for c in self.clients.values() if isinstance(c, key)), None)


def build_agent(client: ThingClient) -> BaseAgent:
    registry = declared_things()
    app = App(client)
    registry.structure_registry.bind(app)
    bound = registry.structure_registry.get_fullfilled_structure("@things/thing")
    assert bound.aexpand is not expand_thing, "the expander was given its client"
    return BaseAgent(
        name=client.owner,
        transport=MemoryAgentTransport(),
        app_registry=registry,
        bound_app=app,
    )


@pytest.mark.asyncio
async def test_expands_through_the_bound_client() -> None:
    agent = build_agent(ThingClient("A"))

    def owner(thing: Thing) -> str:
        """Who fetched it."""
        return f"{thing.id}@{thing.owner}"

    agent.app_registry.register(owner)
    agent.collect_from_registry()

    assert await run_assignment(agent, assign("owner", thing=ref("1"))) == {
        "return0": "1@A"
    }


@pytest.mark.asyncio
async def test_nested_ports_keep_the_bound_client() -> None:
    """list[dict[str, Thing]] is the recursion shape that lost the app before."""
    client = BatchingThingClient("A")
    agent = build_agent(client)

    def owners(groups: list[dict[str, Thing]]) -> str:
        """Every owner, in order."""
        return ",".join(f"{t.id}@{t.owner}" for g in groups for t in g.values())

    agent.app_registry.register(owners)
    agent.collect_from_registry()

    returns = await run_assignment(
        agent,
        assign("owners", groups=[{"a": ref("1"), "b": ref("2")}, {"c": ref("3")}]),
    )
    assert returns == {"return0": "1@A,2@A,3@A"}
    assert client.batches == [["1", "2", "3"]], (
        "one batched request, through the client"
    )


@pytest.mark.asyncio
async def test_one_declaration_binds_independently_per_run() -> None:
    library = declared_things().structure_registry

    one = library.bound({"things": ThingClient("A")})
    two = library.bound({"things": ThingClient("B")})

    fetched = await asyncio.gather(
        one.get_fullfilled_structure("@things/thing").expand("1"),
        two.get_fullfilled_structure("@things/thing").expand("2"),
    )
    assert [t.owner for t in fetched] == ["A", "B"]
    assert library.get_fullfilled_structure("@things/thing").aexpand is expand_thing


@pytest.mark.asyncio
async def test_an_unbound_registry_says_so_at_expansion() -> None:
    registry = declared_things().structure_registry

    # Unbound, the expander still wants its client: nothing has been applied.
    with pytest.raises(TypeError, match="things"):
        await registry.get_fullfilled_structure("@things/thing").expand("1")


def test_binding_without_the_client_fails_at_bind_time() -> None:
    registry = declared_things().structure_registry

    with pytest.raises(StructureClientError, match="ThingClient"):
        registry.bound(App().services)


@pytest.mark.asyncio
async def test_hand_registered_structures_are_untouched_by_binding() -> None:
    """Only a declared structure has a service client; one registered by hand
    keeps expanding itself, bound or not, and binding does not ask for a client."""

    async def aexpand(id: str) -> Thing:
        return Thing(id, "self-resolved")

    async def ashrink(thing: Thing) -> str:
        return thing.id

    registry = StructureRegistry()
    registry.register_as_structure(
        Thing, "@things/thing", aexpand=aexpand, ashrink=ashrink
    )
    bound = registry.bound({})
    structure = bound.get_fullfilled_structure("@things/thing")
    assert structure.aexpand is aexpand
    thing = await structure.expand("1")
    assert thing.owner == "self-resolved"
