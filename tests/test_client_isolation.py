"""The rekuest client keeps to one app's registry, and calls through what is its own.

The arkitekt service builder handing the app's registry to the agent is covered from
the arkitekt side, where a real app can be built.
"""



from rekuest.app import AppRegistry
from rekuest.client.postman import GraphQLPostman
from rekuest.client.rath import RekuestRath
from rekuest.client.client import Rekuest
from rekuest.structures.registry import StructureRegistry

from .conftest import DirectSucceedingLink


def build_client(registry: AppRegistry) -> Rekuest:
    rath = RekuestRath(link=DirectSucceedingLink())
    return Rekuest(
        rath=rath,
        postman=GraphQLPostman(rath=rath),
        structure_registry=registry.structure_registry,
    )


def test_client_has_exactly_one_structure_registry() -> None:
    """It used to default to the process-wide one while ``register`` used the agent's."""
    registry = AppRegistry()
    client = build_client(registry)

    assert client.structure_registry is registry.structure_registry
    assert client.structure_registry is not build_client(AppRegistry()).structure_registry


def test_bare_state_decorator_keeps_the_given_structure_registry() -> None:
    """The bare form re-resolved the structure registry to the default on recursion."""
    from rekuest.state.decorator import state

    registry = AppRegistry()
    structures = StructureRegistry()

    class Bare:
        value: int = 0

    state(Bare, registry=registry, structure_reg=structures)

    assert registry.state_registry_schemas["Bare"] is structures


