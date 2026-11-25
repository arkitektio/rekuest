"""Structures are declared, and a type nobody declared is refused where it is used.

An identifier is a wire contract with the rekuest server, which only accepts
``@package/key``. Everything here fails (or used to fail silently) long before a
server is involved.
"""

import pytest
from arkitekt_spec.declare.client import client_of
from arkitekt_spec.declare.structures.declare import structure_of
from arkitekt_spec.declare.structures.errors import (
    StructureDefinitionError,
    StructureRegistryError,
)
from arkitekt_spec.declare.structures.registry import StructureRegistry
from arkitekt_spec.declare.structures.types import is_valid_identifier
from arkitekt_spec.declare.structures.utils import id_shrink

from rekuest.api.schema import Agent, DetailMaterializedBlok
from rekuest.client.client import Rekuest
from rekuest.structures import SUPERVISOR, expand_agent, expand_materialized_blok


async def aget_thing(id: str) -> "Thing":
    return Thing(id)


class Thing:
    def __init__(self, id: str) -> None:
        self.id = id


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
    assert "is 'tests' imported" in message
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
# rekuest's own structures
# --------------------------------------------------------------------------- #


def test_rekuests_structures_are_owned_by_the_rekuest_client() -> None:
    owner = client_of(Rekuest)
    assert owner is not None

    assert {s.identifier for s in owner.structures} >= {
        "@rekuest/implementation",
        "@rekuest/action",
        "@rekuest/agent",
        "@rekuest/materialized_blok",
    }
    assert all(s.owner is owner for s in owner.structures)


@pytest.mark.asyncio
async def test_a_materialized_blok_is_a_structure_under_the_identifier_orkestrator_links() -> (
    None
):
    """``@rekuest/materialized_blok``, underscore and all: what a room message attaches."""
    structure = structure_of(DetailMaterializedBlok)
    assert structure is not None
    assert structure.identifier == "@rekuest/materialized_blok"

    class FakeRekuest:
        def __init__(self) -> None:
            self.asked: list[str] = []

        async def aget_materialized_blok(self, id: str) -> str:
            self.asked.append(id)
            return id

    client = FakeRekuest()

    assert await expand_materialized_blok("7", client) == "7"  # type: ignore[arg-type]
    assert client.asked == ["7"]


@pytest.mark.asyncio
async def test_an_agent_is_a_structure_described_by_its_supervisor() -> None:
    """``@rekuest/agent``: what a supervisor's actions take, narrowed by ``@rekuest/supervisor``."""
    structure = structure_of(Agent)
    assert structure is not None
    assert structure.identifier == "@rekuest/agent"

    def agent(supervisor: str | None) -> Agent:
        return Agent.model_validate(
            {
                "id": "1",
                "name": "segmentation",
                "hash": "h",
                "supervisor": supervisor,
                "connected": True,
                "active": True,
                "client": {"id": "3", "clientId": "lok-client"},
                "user": {"sub": "1"},
                "organization": {"identifier": "lok#lab"},
                "memoryShelve": {"id": "9"},
            }
        )

    assert structure.describe is not None
    assert structure.describe(agent("plugin_engine")) == {SUPERVISOR: "plugin_engine"}
    # None declared is no descriptor: a Requires on it is then not tested, never failed.
    assert structure.describe(agent(None)) == {}
    assert agent(None).client.client_id == "lok-client"

    class FakeRekuest:
        def __init__(self) -> None:
            self.asked: list[str] = []

        async def aget_agent(self, id: str) -> str:
            self.asked.append(id)
            return id

    client = FakeRekuest()

    assert await expand_agent("7", client) == "7"  # type: ignore[arg-type]
    assert client.asked == ["7"]
