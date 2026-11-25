"""A state keeps the structure registry it was declared with."""

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.structures.registry import StructureRegistry


def test_the_state_decorator_keeps_the_structure_registry_it_was_given() -> None:
    """A foreign structure registry survives, rather than being re-resolved to the app's.

    Calls ``declare_state`` directly on purpose: this is the one thing
    ``AppRegistry.state`` cannot express, because it supplies ``structure_reg=self.structure_registry``
    by design -- an app's structures are the app's. That is exactly what is pinned here.
    """
    from arkitekt_spec.declare.state.decorator import declare_state

    registry = AppRegistry()
    structures = StructureRegistry()

    class Bare:
        value: int = 0

    declare_state(Bare, registry=registry, structure_reg=structures)

    assert registry.state_registry_schemas["Bare"] is structures
