"""rekuest's side of structure clients: a declared structure's expander asks for
the client of the service that declared it, binding hands it that client, and
fetching many goes through the client when it can.

The client mixin itself (``rath.expansion.ExpandsStructures``: dispatch by
identifier, the gather default, ``obj.id``) is tested in rath. Here the clients
use it only as a real service client would.
"""

from collections.abc import Sequence
from typing import Any

import pytest
from rath.expansion import ExpandsStructures, UnknownStructureError

from rekuest.app import AppRegistry
from rekuest.structures.client import StructureClient
from rekuest.structures.errors import StructureClientError, StructureDefinitionError
from rekuest.structures.registry import StructureRegistry
from rekuest.structures.types import FullFilledStructure

from .service_helpers import with_client


class Thing:
    def __init__(self, id: str, by: str = "") -> None:
        self.id = id
        self.by = by


class Other:
    def __init__(self, id: str) -> None:
        self.id = id


class ThingsClient(ExpandsStructures):
    """Two structures, one expander each; records what it is asked."""

    def __init__(self, name: str = "things") -> None:
        self.name = name
        self.expanded: list[tuple[str, str]] = []
        self.many: list[tuple[str, list[str]]] = []
        self.shrunk: list[tuple[str, Any]] = []

    async def aget_thing(self, id: str) -> Thing:
        self.expanded.append(("@things/thing", id))
        return Thing(id, self.name)

    async def aget_other(self, id: str) -> Other:
        self.expanded.append(("@things/other", id))
        return Other(id)

    EXPANDERS = {"@things/thing": aget_thing, "@things/other": aget_other}


class BatchingClient(ThingsClient):
    """Fetches several ids in one request, and shrinks to something that is not ``obj.id``."""

    def __init__(self, name: str = "things", short: bool = False) -> None:
        super().__init__(name)
        self.short = short

    async def aexpand_many(self, identifier: str, ids: Sequence[str]) -> list[Any]:
        self.many.append((identifier, list(ids)))
        if self.short:
            return []
        return [Thing(id, f"{self.name}:batch") for id in ids]

    async def ashrink(self, identifier: str, obj: Any) -> str:  # noqa: ANN401
        self.shrunk.append((identifier, obj))
        return f"shrunk-{obj.id}"


def declared(*classes: tuple[type, str], service: str = "things") -> StructureRegistry:
    """Declared as the things package does: its service returns ``ThingsClient``,
    and each structure's expander (and shrinker) asks for one and goes through it
    by identifier."""
    app = AppRegistry()
    with_client(app, ThingsClient, service)

    def functions(identifier: str) -> tuple[Any, Any]:
        async def expand(id: str, things: ThingsClient) -> Any:  # noqa: ANN401
            return await things.aexpand(identifier, id)

        async def shrink(obj: Any, things: ThingsClient) -> str:  # noqa: ANN401
            return await things.ashrink(identifier, obj)

        return expand, shrink

    for cls, identifier in classes:
        expand, shrink = functions(identifier)
        app.structure(identifier, cls=cls, shrink=shrink)(expand)
    return app.structure_registry


def thing_structure(client: Any) -> FullFilledStructure:  # noqa: ANN401
    registry = declared((Thing, "@things/thing"), (Other, "@things/other"))
    return registry.bound({"things": client}).get_fullfilled_structure("@things/thing")


# --------------------------------------------------------------------------- #
# A declared structure delegates to its bound client
# --------------------------------------------------------------------------- #


def test_an_expands_structures_client_is_a_structure_client() -> None:
    assert isinstance(ThingsClient(), StructureClient)
    assert not isinstance(object(), StructureClient)


@pytest.mark.asyncio
async def test_expand_asks_the_client_by_the_structures_identifier() -> None:
    client = ThingsClient()
    bound = declared((Thing, "@things/thing"), (Other, "@things/other")).bound(
        {"things": client}
    )

    thing = await bound.get_fullfilled_structure("@things/thing").expand("1")
    other = await bound.get_fullfilled_structure("@things/other").expand("2")

    assert isinstance(thing, Thing) and thing.by == "things"
    assert isinstance(other, Other)
    assert client.expanded == [("@things/thing", "1"), ("@things/other", "2")]


@pytest.mark.asyncio
async def test_a_client_that_names_no_expander_fails_the_expansion() -> None:
    """Declared by a service whose client does not know the identifier."""

    class Partial(ThingsClient):
        EXPANDERS = {"@things/other": ThingsClient.aget_other}

    structure = thing_structure(Partial())

    with pytest.raises(
        UnknownStructureError, match="Partial cannot expand '@things/thing'"
    ):
        await structure.expand("1")


@pytest.mark.asyncio
async def test_the_default_aexpand_many_answers_in_order() -> None:
    client = ThingsClient()
    structure = thing_structure(client)
    assert structure.aexpand_many is not None, "borrowed from the client"

    found = await structure.expand_many(["3", "1", "2"])

    assert [thing.id for thing in found] == ["3", "1", "2"]
    assert sorted(id for _, id in client.expanded) == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_an_overridden_aexpand_many_is_what_expand_many_calls() -> None:
    client = BatchingClient()
    structure = thing_structure(client)

    found = await structure.expand_many(["1", "2"])

    assert client.many == [("@things/thing", ["1", "2"])]
    assert client.expanded == []
    assert [(thing.id, thing.by) for thing in found] == [
        ("1", "things:batch"),
        ("2", "things:batch"),
    ]


@pytest.mark.asyncio
async def test_a_client_answering_the_wrong_number_of_values_is_refused() -> None:
    structure = thing_structure(BatchingClient(short=True))

    with pytest.raises(ValueError, match="asked for 2 ids and got 0"):
        await structure.expand_many(["1", "2"])


@pytest.mark.asyncio
async def test_shrink_goes_through_the_client() -> None:
    client = BatchingClient()
    structure = thing_structure(client)
    thing = Thing("7")

    assert await structure.shrink(thing) == "shrunk-7"
    assert client.shrunk == [("@things/thing", thing)]


@pytest.mark.asyncio
async def test_the_default_shrink_is_the_objects_id() -> None:
    assert await thing_structure(ThingsClient()).shrink(Thing("7")) == "7"


# --------------------------------------------------------------------------- #
# Binding
# --------------------------------------------------------------------------- #


def test_binding_without_a_client_of_the_wanted_class_names_the_structure() -> None:
    """The expander asks for a ``ThingsClient`` by class; a run without one cannot bind."""

    class Elsewhere:
        """A client of some other service."""

    registry = declared((Thing, "@things/thing"))

    with pytest.raises(StructureClientError) as raised:
        registry.bound({"elsewhere": Elsewhere()})

    message = str(raised.value)
    assert "'@things/thing'" in message
    assert "asks for a ThingsClient" in message
    assert registry.get_fullfilled_structure("@things/thing") is not None


def test_binding_leaves_the_declaration_unbound() -> None:
    registry = declared((Thing, "@things/thing"))
    client = ThingsClient()
    unbound = registry.get_fullfilled_structure("@things/thing")

    bound = registry.bound({"things": client})

    assert bound.get_fullfilled_structure("@things/thing") is not unbound
    assert bound.find_for_cls(Thing) is bound.get_fullfilled_structure("@things/thing")
    assert (
        bound.get_fullfilled_structure("@things/thing").aexpand is not unbound.aexpand
    )
    assert registry.get_fullfilled_structure("@things/thing") is unbound


@pytest.mark.asyncio
async def test_a_declared_structure_of_an_unbound_registry_cannot_expand() -> None:
    """Nothing handed the expander its client, so it is still asking for one."""
    structure = declared((Thing, "@things/thing")).get_fullfilled_structure(
        "@things/thing"
    )

    for use in (
        lambda: structure.expand("1"),
        lambda: structure.expand_many(["1"]),
        lambda: structure.shrink(Thing("1")),
    ):
        with pytest.raises(TypeError, match="things"):
            await use()


# --------------------------------------------------------------------------- #
# A structure registered by hand still carries its own functions
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_hand_registered_structure_uses_its_own_expand_and_shrink() -> None:
    expanded: list[str] = []

    async def aexpand(id: str) -> Thing:
        expanded.append(id)
        return Thing(id, "by-hand")

    async def ashrink(thing: Thing) -> str:
        return f"hand-{thing.id}"

    registry = StructureRegistry()
    registry.register_as_structure(
        Thing, "@things/thing", aexpand=aexpand, ashrink=ashrink
    )
    # Binding skips it: it has no service client, and asks for none.
    structure = registry.bound({}).get_fullfilled_structure("@things/thing")

    assert not structure.declared
    assert structure.aexpand_many is None
    assert (await structure.expand("1")).by == "by-hand"
    assert [t.id for t in await structure.expand_many(["2", "3"])] == ["2", "3"]
    assert await structure.shrink(Thing("4")) == "hand-4"
    assert expanded == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_a_hand_registered_structure_with_aexpand_many_batches_through_it() -> (
    None
):
    calls: list[list[str]] = []

    async def aexpand(id: str) -> Thing:
        raise AssertionError("expand_many must not fall back per id")

    async def aexpand_many(ids: Sequence[str]) -> list[Thing]:
        calls.append(list(ids))
        return [Thing(id) for id in ids]

    async def ashrink(thing: Thing) -> str:
        return thing.id

    structure = FullFilledStructure(
        cls=Thing,
        identifier="@things/thing",
        description=None,
        predicate=lambda value: isinstance(value, Thing),
        convert_default=None,
        default_widget=None,
        default_returnwidget=None,
        aexpand=aexpand,
        ashrink=ashrink,
        aexpand_many=aexpand_many,
    )

    assert structure.aexpand_many is not None
    assert [t.id for t in await structure.expand_many(["1", "2"])] == ["1", "2"]
    assert calls == [["1", "2"]]


def test_a_hand_registered_structure_without_aexpand_is_refused() -> None:
    with pytest.raises(StructureDefinitionError, match="has no way to expand an id"):
        FullFilledStructure(
            cls=Thing,
            identifier="@things/thing",
            description=None,
            predicate=lambda value: isinstance(value, Thing),
            convert_default=None,
            default_widget=None,
            default_returnwidget=None,
        )
