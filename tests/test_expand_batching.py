"""Ids of one structure that are expanded together are fetched together.

A list of N structures used to be N requests. A structure that can fetch several
ids at once gets them in one, and everything else behaves as it did.
"""

from typing import Any

import pytest
from rath.expansion import ExpandsStructures

from rekuest.actors.types import Shelver
from rekuest.app import AppRegistry
from rekuest.definition.define import prepare_definition
from rekuest.structures.errors import ExpandingError
from rekuest.structures.registry import StructureRegistry
from rekuest.structures.serialization.actor import expand_inputs
from rekuest.structures.serialization.expand import aexpand_returns

from .service_helpers import with_client


class Image:
    def __init__(self, id: str) -> None:
        self.id = id

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Image) and other.id == self.id

    def __repr__(self) -> str:
        return f"Image({self.id})"


class Table(Image):
    pass


class Fetches:
    """Records every request a structure's expanders make."""

    def __init__(
        self, cls: type, missing: tuple[str, ...] = (), broken: bool = False
    ) -> None:
        self.cls, self.missing, self.broken = cls, missing, broken
        self.single: list[str] = []
        self.single_ctx: list[Any] = []
        self.many: list[tuple[list[str], Any]] = []

    async def aexpand(self, client: "Client", id: str) -> Any:
        self.single.append(id)
        self.single_ctx.append(client)
        return self.cls(id)

    async def aexpand_many(self, client: "Client", ids: Any) -> list[Any]:
        self.many.append((list(ids), client))
        if self.broken:
            raise ConnectionError("the service is down")
        return [None if id in self.missing else self.cls(id) for id in ids]


class Client(ExpandsStructures):
    """The images service's client: the structures below are expanded through one.

    Each identifier's requests are recorded by its :class:`Fetches`. A batching
    client fetches several ids in one request; one that is not uses the default
    ``aexpand_many``, one ``aexpand`` per id.
    """

    def __init__(
        self, name: str, fetches: dict[str, Fetches], batched: bool = True
    ) -> None:
        self.name = name
        self.fetches = fetches
        self.batched = batched
        self.EXPANDERS = {identifier: f.aexpand for identifier, f in fetches.items()}

    async def aexpand_many(self, identifier: str, ids: Any) -> Any:
        if not self.batched:
            return await super().aexpand_many(identifier, ids)
        return await self.fetches[identifier].aexpand_many(self, ids)


def client_for(
    name: str, *declared: tuple[type, str, Fetches], batched: bool = True
) -> Client:
    return Client(name, {identifier: f for _, identifier, f in declared}, batched)


def declared_registry(*declared: tuple[type, str, Fetches]) -> StructureRegistry:
    """Declared the way the images package does: its service, then each structure,
    whose expander asks for the ``Client`` the service returns and fetches through
    it. Unbound: no run has handed the expanders a client yet."""
    app = AppRegistry()
    with_client(app, Client, "images")

    def expander(fetches: Fetches) -> Any:
        async def expand(id: str, images: Client) -> Any:
            return await fetches.aexpand(images, id)

        return expand

    for cls, identifier, fetches in declared:
        app.structure(identifier, cls=cls)(expander(fetches))
    return app.structure_registry


def registry_with(
    *declared: tuple[type, str, Fetches],
    batched: bool = True,
    client: Client | None = None,
) -> StructureRegistry:
    """The declared structures, bound to ``client`` (or one named "bound")."""
    bound_to = client or client_for("bound", *declared, batched=batched)
    return declared_registry(*declared).bound({"images": bound_to})


def ref(identifier: str, id: str) -> dict[str, str]:
    return {"__identifier": identifier, "object": id}


def stack(images: list[Image]) -> None:
    """Stack."""


def overlay(base: Image, top: Image, tables: list[Table]) -> None:
    """Overlay."""


def index(by_name: dict[str, Image]) -> None:
    """Index."""


def produce() -> list[Image]:
    """Produce."""
    return []


@pytest.mark.asyncio
async def test_a_list_is_one_request(mock_shelver: Shelver) -> None:
    fetches = Fetches(Image)
    client = client_for("current", (Image, "@images/image", fetches))
    registry = registry_with((Image, "@images/image", fetches), client=client)
    definition = prepare_definition(stack, structure_registry=registry)

    args = await expand_inputs(
        definition,
        {"images": [ref("@images/image", id) for id in ("1", "2", "3")]},
        structure_registry=registry,
        shelver=mock_shelver,
    )

    assert args == {"images": [Image("1"), Image("2"), Image("3")]}
    assert fetches.many == [(["1", "2", "3"], client)]
    assert fetches.single == []


@pytest.mark.asyncio
async def test_sibling_arguments_share_the_request_and_each_structure_gets_its_own(
    mock_shelver: Shelver,
) -> None:
    images, tables = Fetches(Image), Fetches(Table)
    registry = registry_with(
        (Image, "@images/image", images), (Table, "@images/table", tables)
    )
    definition = prepare_definition(overlay, structure_registry=registry)

    args = await expand_inputs(
        definition,
        {
            "base": ref("@images/image", "1"),
            "top": ref("@images/image", "2"),
            "tables": [ref("@images/table", "7"), ref("@images/table", "8")],
        },
        structure_registry=registry,
        shelver=mock_shelver,
    )

    assert args == {
        "base": Image("1"),
        "top": Image("2"),
        "tables": [Table("7"), Table("8")],
    }
    assert [ids for ids, _ in images.many] == [["1", "2"]]
    assert [ids for ids, _ in tables.many] == [["7", "8"]]


@pytest.mark.asyncio
async def test_the_same_id_twice_is_fetched_once(mock_shelver: Shelver) -> None:
    fetches = Fetches(Image)
    registry = registry_with((Image, "@images/image", fetches))
    definition = prepare_definition(stack, structure_registry=registry)

    args = await expand_inputs(
        definition,
        {"images": [ref("@images/image", id) for id in ("1", "2", "1")]},
        structure_registry=registry,
        shelver=mock_shelver,
    )

    assert args["images"] == [Image("1"), Image("2"), Image("1")]
    assert [ids for ids, _ in fetches.many] == [["1", "2"]]


@pytest.mark.asyncio
async def test_the_values_of_a_dict_are_one_request_too(mock_shelver: Shelver) -> None:
    fetches = Fetches(Image)
    registry = registry_with((Image, "@images/image", fetches))
    definition = prepare_definition(index, structure_registry=registry)

    args = await expand_inputs(
        definition,
        {"by_name": {"a": ref("@images/image", "1"), "b": ref("@images/image", "2")}},
        structure_registry=registry,
        shelver=mock_shelver,
    )

    assert args == {"by_name": {"a": Image("1"), "b": Image("2")}}
    assert len(fetches.many) == 1


@pytest.mark.asyncio
async def test_a_client_that_cannot_batch_expands_one_by_one_as_before(
    mock_shelver: Shelver,
) -> None:
    fetches = Fetches(Image)
    registry = registry_with((Image, "@images/image", fetches), batched=False)
    definition = prepare_definition(stack, structure_registry=registry)

    args = await expand_inputs(
        definition,
        {"images": [ref("@images/image", id) for id in ("1", "2")]},
        structure_registry=registry,
        shelver=mock_shelver,
    )

    assert args == {"images": [Image("1"), Image("2")]}
    assert sorted(fetches.single) == ["1", "2"] and fetches.many == []


@pytest.mark.asyncio
async def test_a_hand_registered_structure_skips_the_batcher(
    mock_shelver: Shelver,
) -> None:
    """Only a structure registered by hand without ``aexpand_many`` does not batch
    now; the batcher expands its ids directly, one each."""
    from rekuest.structures.utils import id_shrink

    fetches = Fetches(Image)

    async def aexpand(id: str) -> Image:
        return await fetches.aexpand(None, id)  # type: ignore[arg-type]

    registry = StructureRegistry()
    structure = registry.register_as_structure(
        Image, "@images/image", aexpand=aexpand, ashrink=id_shrink
    )
    assert structure.aexpand_many is None
    definition = prepare_definition(stack, structure_registry=registry)

    args = await expand_inputs(
        definition,
        {"images": [ref("@images/image", id) for id in ("1", "2")]},
        structure_registry=registry,
        shelver=mock_shelver,
    )

    assert args == {"images": [Image("1"), Image("2")]}
    assert sorted(fetches.single) == ["1", "2"] and fetches.many == []


@pytest.mark.asyncio
async def test_nested_structures_are_expanded_through_the_bound_client_too(
    mock_shelver: Shelver,
) -> None:
    """The elements of a list used to lose the app, and resolve their client ambiently."""
    fetches = Fetches(Image)
    client = client_for("current", (Image, "@images/image", fetches), batched=False)
    registry = registry_with(
        (Image, "@images/image", fetches), batched=False, client=client
    )
    definition = prepare_definition(index, structure_registry=registry)

    await expand_inputs(
        definition,
        {"by_name": {"a": ref("@images/image", "1")}},
        structure_registry=registry,
        shelver=mock_shelver,
    )

    assert fetches.single_ctx == [client]


@pytest.mark.asyncio
async def test_a_missing_id_says_which_one_and_where(mock_shelver: Shelver) -> None:
    fetches = Fetches(Image, missing=("2",))
    registry = registry_with((Image, "@images/image", fetches))
    definition = prepare_definition(stack, structure_registry=registry)

    with pytest.raises(ExpandingError) as raised:
        await expand_inputs(
            definition,
            {"images": [ref("@images/image", id) for id in ("1", "2", "3")]},
            structure_registry=registry,
            shelver=mock_shelver,
        )

    chain = _chain(raised.value)
    assert "There is no @images/image with id '2'" in chain
    assert "images[1]" in chain


@pytest.mark.asyncio
async def test_a_failed_request_fails_the_expansion_with_its_cause(
    mock_shelver: Shelver,
) -> None:
    fetches = Fetches(Image, broken=True)
    registry = registry_with((Image, "@images/image", fetches))
    definition = prepare_definition(stack, structure_registry=registry)

    with pytest.raises(ExpandingError) as raised:
        await expand_inputs(
            definition,
            {"images": [ref("@images/image", id) for id in ("1", "2")]},
            structure_registry=registry,
            shelver=mock_shelver,
        )

    assert "the service is down" in _chain(raised.value)
    assert len(fetches.many) == 1


@pytest.mark.asyncio
async def test_returns_on_the_calling_side_are_batched_as_well() -> None:
    fetches = Fetches(Image)
    client = client_for("current", (Image, "@images/image", fetches))
    registry = registry_with((Image, "@images/image", fetches), client=client)
    definition = prepare_definition(produce, structure_registry=registry)
    key = definition.returns[0].key

    (images,) = await aexpand_returns(
        definition,
        {key: [ref("@images/image", id) for id in ("1", "2", "3")]},
        structure_registry=registry,
    )

    assert images == [Image("1"), Image("2"), Image("3")]
    assert fetches.many == [(["1", "2", "3"], client)]


def _chain(error: BaseException | None) -> str:
    parts = []
    while error is not None:
        parts.append(str(error))
        error = error.__cause__
    return " <- ".join(parts)


# --------------------------------------------------------------------------- #
# With rath's federated expanders: a list port is one `_entities` request
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_list_of_federated_structures_is_one_entities_request(
    mock_shelver: Shelver,
) -> None:
    from types import SimpleNamespace

    from pydantic import BaseModel
    from rath.turms.federation import federated

    class Flow(BaseModel):
        id: str

        class Meta:
            document = "fragment Flow on Flow { id }"
            name = "Flow"
            type = "Flow"

    class Rath:
        def __init__(self) -> None:
            self.requests: list[Any] = []

        async def aquery(self, query: str, variables: dict[str, Any]) -> Any:
            self.requests.append(variables["representations"])
            return SimpleNamespace(
                data={
                    "entities": [
                        {"id": rep["id"]} for rep in variables["representations"]
                    ]
                }
            )

    def run(flows: list[Flow]) -> None:
        """Run."""

    via_entities = federated(Flow)

    class Fluss(ExpandsStructures):
        EXPANDERS = {"@fluss/flow": via_entities["aexpand"]}

        def __init__(self) -> None:
            self.rath = Rath()

        async def aexpand_many(self, identifier: str, ids: Any) -> Any:
            return await via_entities["aexpand_many"](self, ids)

    app = Fluss()
    package = AppRegistry()
    with_client(package, Fluss, "fluss")

    @package.structure("@fluss/flow")
    async def expand_flow(id: str, fluss: Fluss) -> Flow:
        """A flow, fetched through the fluss client."""
        return await via_entities["aexpand"](fluss, id)

    registry = package.structure_registry.bound({"fluss": app})
    definition = prepare_definition(run, structure_registry=registry)

    args = await expand_inputs(
        definition,
        {"flows": [ref("@fluss/flow", id) for id in ("1", "2", "3")]},
        structure_registry=registry,
        shelver=mock_shelver,
    )

    assert [flow.id for flow in args["flows"]] == ["1", "2", "3"]
    assert app.rath.requests == [
        [{"__typename": "Flow", "id": id} for id in ("1", "2", "3")]
    ]


# --------------------------------------------------------------------------- #
# How far the batching reaches
# --------------------------------------------------------------------------- #


def grouped(groups: list[dict[str, Image]]) -> None:
    """Grouped."""


def mixed(cover: Image, groups: list[list[Image]]) -> None:
    """A structure at one level and more at another."""


@pytest.mark.asyncio
async def test_nesting_does_not_split_the_batch(mock_shelver: Shelver) -> None:
    """Every leaf asks before the flush runs, however deep the containers go."""
    fetches = Fetches(Image)
    registry = registry_with((Image, "@images/image", fetches))
    definition = prepare_definition(grouped, structure_registry=registry)

    args = await expand_inputs(
        definition,
        {
            "groups": [
                {"a": ref("@images/image", "1"), "b": ref("@images/image", "2")},
                {"c": ref("@images/image", "3")},
            ]
        },
        structure_registry=registry,
        shelver=mock_shelver,
    )

    assert args == {"groups": [{"a": Image("1"), "b": Image("2")}, {"c": Image("3")}]}
    assert [ids for ids, _ in fetches.many] == [["1", "2", "3"]]


@pytest.mark.asyncio
async def test_leaves_at_different_depths_take_one_request_each_depth(
    mock_shelver: Shelver,
) -> None:
    """The known limit, pinned rather than claimed away.

    The flush is scheduled when the first id arrives and runs on the next turn of
    the loop, by which time everything at *that* depth has asked but a deeper leaf
    is still descending. Batching per depth is what the loop can tell us without
    tracking when the whole walk is quiescent; N ids still become one request per
    depth rather than N, and a single list port -- the case this is for -- is one.
    """
    fetches = Fetches(Image)
    registry = registry_with((Image, "@images/image", fetches))
    definition = prepare_definition(mixed, structure_registry=registry)

    args = await expand_inputs(
        definition,
        {
            "cover": ref("@images/image", "0"),
            "groups": [
                [ref("@images/image", "1"), ref("@images/image", "2")],
                [ref("@images/image", "3")],
            ],
        },
        structure_registry=registry,
        shelver=mock_shelver,
    )

    assert args["cover"] == Image("0")
    assert args["groups"] == [[Image("1"), Image("2")], [Image("3")]]
    assert [ids for ids, _ in fetches.many] == [["0"], ["1", "2", "3"]]


# --------------------------------------------------------------------------- #
# Nested ports go through the same client
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_structure_two_containers_deep_still_goes_through_its_bound_client(
    mock_shelver: Shelver,
) -> None:
    """The client is bound to the structure, so depth cannot lose it.

    This is what used to break: the app was threaded by hand and both `_expand`
    helpers dropped it while recursing. Nothing is threaded any more.
    """
    fetches = Fetches(Image)
    client = client_for("current", (Image, "@images/image", fetches))
    registry = registry_with((Image, "@images/image", fetches), client=client)
    definition = prepare_definition(grouped, structure_registry=registry)

    args = await expand_inputs(
        definition,
        {
            "groups": [
                {"a": ref("@images/image", "1")},
                {"b": ref("@images/image", "2")},
            ]
        },
        structure_registry=registry,
        shelver=mock_shelver,
    )

    assert args == {"groups": [{"a": Image("1")}, {"b": Image("2")}]}
    assert [ctx for _, ctx in fetches.many] == [client]


@pytest.mark.asyncio
async def test_an_unbound_registry_says_so_at_expansion(
    mock_shelver: Shelver,
) -> None:
    """The control: no client bound, so the expander is still asking for one."""
    fetches = Fetches(Image)
    registry = declared_registry((Image, "@images/image", fetches))
    definition = prepare_definition(grouped, structure_registry=registry)

    with pytest.raises(ExpandingError) as raised:
        await expand_inputs(
            definition,
            {"groups": [{"a": ref("@images/image", "1")}]},
            structure_registry=registry,
            shelver=mock_shelver,
        )

    assert "images" in _chain(raised.value)
    assert fetches.many == [] and fetches.single == []


@pytest.mark.asyncio
async def test_the_batcher_holds_its_flush_until_it_is_done() -> None:
    """The event loop keeps only weak references to tasks, so the batcher keeps one."""
    import asyncio

    from rekuest.structures.serialization.batching import ExpandBatcher

    batcher = ExpandBatcher()
    started, release = asyncio.Event(), asyncio.Event()

    class Structure:
        identifier = "@test/held"
        aexpand_many = (
            True  # anything but None: the batcher only asks whether it can batch
        )

        async def expand_many(self, ids):
            started.set()
            await release.wait()
            return list(ids)

    waiter = asyncio.ensure_future(batcher.load(Structure(), "1"))  # type: ignore[arg-type]
    await started.wait()
    assert len(batcher._flushing) == 1
    release.set()
    assert await waiter == "1"
    await asyncio.sleep(0)
    assert batcher._flushing == set()
