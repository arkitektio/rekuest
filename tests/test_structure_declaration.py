"""Structures are declared, and a type nobody declared is refused where it is used.

An identifier is a wire contract with the rekuest server, which only accepts
``@package/key``. Everything here fails (or used to fail silently) long before a
server is involved.
"""

from typing import Any

import pytest
from rath.expansion import ExpandsStructures

from rekuest.app import AppRegistry
from rekuest.arkitekt import registry as rekuest_registry
from rekuest.structures.errors import (
    StructureDefinitionError,
    StructureOverwriteError,
    StructureRegistryError,
)
from rekuest.structures.registry import StructureRegistry
from rekuest.structures.types import is_valid_identifier
from rekuest.structures.utils import id_shrink

from .service_helpers import with_client


async def aget_thing(id: str) -> "Thing":
    return Thing(id)


class Thing:
    def __init__(self, id: str) -> None:
        self.id = id


class OtherThing(Thing):
    pass


class ThingClient(ExpandsStructures):
    """Expands one id per request; records each."""

    def __init__(self) -> None:
        self.single: list[str] = []

    async def aget_thing(self, id: str) -> Thing:
        self.single.append(id)
        return Thing(id)

    EXPANDERS = {"@things/thing": aget_thing}


class GeneratedEntity:
    """What turms generates for a fetchable type: it names itself by its typename."""

    @classmethod
    def get_identifier(cls) -> str:
        return "GeneratedEntity"

    @classmethod
    async def aexpand(cls, id: str) -> "GeneratedEntity":
        return cls()

    async def ashrink(self) -> str:
        return "1"


class GeneratedFragment:
    """What turms generates for a nested, non-fetchable part of a type."""

    class Meta:
        document = "fragment GeneratedFragment on Entity { name }"
        name = "GeneratedFragment"
        type = "Entity"


class PlainObject:
    pass


# --------------------------------------------------------------------------- #
# The identifier rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "identifier", ["@mikro/arraydataset", "@lovekit/solo_broadcast", "@a.b/c-d"]
)
def test_what_the_server_accepts(identifier: str) -> None:
    assert is_valid_identifier(identifier)


@pytest.mark.parametrize(
    "identifier",
    ["ArrayDataset", "mikro/arraydataset", "@mikro", "@mikro/a/b", "@mikro/ a", ""],
)
def test_what_the_server_refuses(identifier: str) -> None:
    assert not is_valid_identifier(identifier)


def test_a_structure_cannot_be_registered_under_a_name_the_server_refuses() -> None:
    registry = StructureRegistry()

    with pytest.raises(
        StructureDefinitionError, match="'thing' is not a structure identifier"
    ):
        registry.register_as_structure(
            Thing, "thing", aexpand=aget_thing, ashrink=id_shrink
        )


# --------------------------------------------------------------------------- #
# The two ways an undeclared type used to slip through
# --------------------------------------------------------------------------- #


def test_a_fetchable_type_nobody_declared_is_refused_with_the_cause() -> None:
    """It used to register as 'GeneratedEntity', for the server to refuse later."""
    registry = StructureRegistry()

    with pytest.raises(StructureDefinitionError) as raised:
        registry.get_fullfilled_type_for_cls(GeneratedEntity)

    message = str(raised.value)
    assert "nothing declared it" in message
    assert "'GeneratedEntity'" in message and "@package/key" in message
    assert "is 'tests' one of this app's services" in message
    assert registry.find_for_cls(GeneratedEntity) is None


def test_a_generated_fragment_is_not_shelved_as_a_memory_structure() -> None:
    """It used to become '@tests.../generatedfragment', parked in the agent."""
    registry = StructureRegistry()

    with pytest.raises(StructureDefinitionError, match="generated GraphQL fragment"):
        registry.get_fullfilled_type_for_cls(GeneratedFragment)

    assert registry.identifier_memory_structure_map == {}


def test_a_plain_object_is_refused_until_it_is_registered() -> None:
    """It used to become a memory structure on the spot, which is the whole problem.

    An object parked in this agent's shelve is unreadable to every other app, so
    it is something to ask for, not something a misspelt annotation falls into.
    """
    registry = StructureRegistry()

    with pytest.raises(StructureRegistryError) as raised:
        registry.get_fullfilled_type_for_cls(PlainObject)

    assert "not registered" in str(raised.value)
    assert "register_memory_structure" in str(raised.value)
    assert registry.identifier_memory_structure_map == {}

    # Asked for, it works, and the port can name it.
    registry.register_as_memory_structure(PlainObject)
    assert len(registry.identifier_memory_structure_map) == 1
    assert registry.get_fullfilled_type_for_cls(PlainObject) is not None


# --------------------------------------------------------------------------- #
# Declaring: the service first, then the structures its client fetches
# --------------------------------------------------------------------------- #


def things_registry(
    cls: type = Thing, identifier: str = "@things/thing", service: str = "things"
) -> AppRegistry:
    """What a things package ships: a service returning ``ThingClient``, and a structure."""
    registry = AppRegistry()
    with_client(registry, ThingClient, service)

    @registry.structure(identifier, cls=cls)
    async def expand_thing(id: str, things: ThingClient) -> Any:  # noqa: ANN401
        """A thing, by id."""
        return await things.aget_thing(id)

    return registry


def test_declared_structures_belong_to_the_service_whose_client_fetches_them() -> None:
    registry = things_registry().structure_registry

    declared = registry.get_fullfilled_structure("@things/thing")

    assert declared.service == "things" and declared.declared
    assert registry.find_for_cls(Thing) is declared


def test_the_service_is_stated_not_guessed_from_the_identifier() -> None:
    """unlok's structure is '@lok/service': the prefix names no service at all."""
    registry = things_registry(
        identifier="@lok/service", service="unlok"
    ).structure_registry

    assert registry.get_fullfilled_structure("@lok/service").service == "unlok"


def test_a_hand_registered_structure_still_guesses_its_service() -> None:
    registry = StructureRegistry()

    structure = registry.register_as_structure(
        Thing, "@things/thing", aexpand=aget_thing, ashrink=id_shrink
    )

    assert structure.service == "things" and not structure.declared


def test_a_structure_whose_client_no_service_returns_is_refused() -> None:
    """The service comes first: it is what makes ``ThingClient`` a client."""
    registry = AppRegistry()

    with pytest.raises(
        StructureDefinitionError, match="no registered service returns one"
    ):

        @registry.structure("@things/thing")
        async def expand_thing(id: str, things: ThingClient) -> Thing:
            """Wants a client nothing here returns."""
            return await things.aget_thing(id)


def test_taking_the_same_package_in_twice_is_fine() -> None:
    app = AppRegistry()
    package = things_registry()

    app.merge(package)
    app.merge(package)

    assert [s.identifier for s in app.structure_registry.structures()] == [
        "@things/thing"
    ]


def test_two_classes_cannot_claim_one_identifier() -> None:
    app = AppRegistry()
    app.merge(things_registry())

    with pytest.raises(StructureOverwriteError) as raised:
        app.merge(things_registry(cls=OtherThing, service="others"))

    message = str(raised.value)
    assert "'@things/thing' is already registered" in message
    assert "(service 'things')" in message and "(service 'others')" in message
    assert app.structure_registry.get_fullfilled_structure("@things/thing").cls is Thing


def test_a_reloaded_module_may_register_its_class_again() -> None:
    """Reloading makes a new class object of the same name; that is not a conflict."""
    app = AppRegistry()
    app.merge(things_registry())
    reloaded = type(
        "Thing", (), {"__module__": Thing.__module__, "__qualname__": "Thing"}
    )

    app.structure_registry.merge(things_registry(cls=reloaded).structure_registry)

    assert (
        app.structure_registry.get_fullfilled_structure("@things/thing").cls is reloaded
    )


def test_rekuests_own_structures_are_declared_the_same_way() -> None:
    declared = rekuest_registry.structure_registry.structures()

    assert {s.identifier for s in declared} >= {
        "@rekuest/implementation",
        "@rekuest/action",
    }
    assert {(s.service, s.declared) for s in declared} == {("rekuest", True)}


# --------------------------------------------------------------------------- #
# Expanding many -- through the declaring service's client
# --------------------------------------------------------------------------- #


class BatchingThingClient(ThingClient):
    """Also fetches several ids in one request, ``None`` for a missing one."""

    def __init__(self, answer_short: bool = False) -> None:
        super().__init__()
        self.answer_short = answer_short
        self.calls: list[list[str]] = []

    async def aexpand_many(self, identifier: str, ids: Any) -> list[Any]:
        self.calls.append(list(ids))
        if self.answer_short:
            return []
        return [None if id == "missing" else Thing(id) for id in ids]


def bound_structure(client: Any) -> Any:
    registry = things_registry().structure_registry
    return registry.bound({"things": client}).get_fullfilled_structure("@things/thing")


@pytest.mark.asyncio
async def test_expand_many_is_one_call_when_the_client_can() -> None:
    client = BatchingThingClient()
    structure = bound_structure(client)

    found = await structure.expand_many(["1", "missing", "2"])

    assert client.calls == [["1", "missing", "2"]]
    assert client.single == []
    assert [thing and thing.id for thing in found] == ["1", None, "2"]


@pytest.mark.asyncio
async def test_expand_many_falls_back_to_one_expand_per_id() -> None:
    """A client that does not override ``aexpand_many`` expands each id on its own."""
    client = ThingClient()
    structure = bound_structure(client)

    found = await structure.expand_many(["1", "2"])

    assert [thing.id for thing in found] == ["1", "2"]
    assert sorted(client.single) == ["1", "2"]


@pytest.mark.asyncio
async def test_expand_many_must_answer_one_per_id() -> None:
    structure = bound_structure(BatchingThingClient(answer_short=True))

    with pytest.raises(ValueError, match="asked for 2 ids and got 0"):
        await structure.expand_many(["1", "2"])
