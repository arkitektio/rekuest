"""A per-app registry: it holds exactly what its own app declared.

There is no registry above it, and nothing arrives by itself: a structure a client
owns goes in when the app uses that client, anything else has to be registered
here by hand.
"""

from enum import Enum

import pytest
from arkitekt_spec.actions import PortKind
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.assemble import assemble
from arkitekt_spec.declare.bundle import Bundle
from arkitekt_spec.declare.definition.errors import DefinitionError
from arkitekt_spec.declare.structures.errors import StructureRegistryError

from rekuest.api.schema import Implementation


class AppLocalThing:
    """A class only one app knows about."""


class Colour(Enum):
    RED = "red"
    BLUE = "blue"


# --------------------------------------------------------------------------- #
# What an app's registry holds
# --------------------------------------------------------------------------- #


def test_locally_registered_types_stay_in_the_app_that_holds_them() -> None:
    a, b = AppRegistry(), AppRegistry()

    a.register_memory_structure(AppLocalThing)
    a.structure_registry.get_argport_for_cls(AppLocalThing, "x")
    # An enum still derives on demand, so it lands in whichever app met it.
    a.structure_registry.get_argport_for_cls(Colour, "c")

    assert AppLocalThing in a.structure_registry.cls_fullfilled_type_map
    assert Colour in a.structure_registry.cls_fullfilled_type_map
    assert b.structure_registry.find_for_cls(AppLocalThing) is None
    assert b.structure_registry.find_for_cls(Colour) is None


def test_a_function_naming_a_structure_of_an_unused_client_fails_at_registration() -> (
    None
):
    """A generated, fetchable class, but this registry uses no client owning it."""
    app = AppRegistry()

    with pytest.raises(DefinitionError) as raised:

        @app.register
        def needs_rekuest(implementation: Implementation) -> None:
            """Takes a structure no client of this app owns."""

    assert "rekuest" in str(raised.value)
    assert app.is_empty()


def test_an_action_taking_a_rekuest_type_brings_the_rekuest_client() -> None:
    app = Bundle()

    def takes_implementation(implementation: Implementation) -> None:
        """Takes a structure the rekuest client owns."""

    app.register_action(takes_implementation)
    registry = assemble(app)

    assert "takes_implementation" in registry.implementations
    assert "rekuest" in registry.clients
    structure = registry.structure_registry.get_fullfilled_structure(
        "@rekuest/implementation"
    )
    assert structure.client == "rekuest"
    assert structure.cls is Implementation
    port = registry.structure_registry.get_argport_for_cls(Implementation, "x")
    assert port.kind == PortKind.STRUCTURE
    assert port.identifier == "@rekuest/implementation"


# --------------------------------------------------------------------------- #
# Bookkeeping
# --------------------------------------------------------------------------- #


def test_is_empty_tracks_every_kind_of_registration() -> None:
    registry = AppRegistry()
    assert registry.is_empty()

    @registry.startup
    async def boot():  # noqa: ANN202 -- a startup hook's return annotation is its contract
        """Boot."""

    assert not registry.is_empty()


# --------------------------------------------------------------------------- #
# The registry can be checked before anything connects
# --------------------------------------------------------------------------- #
# Ports are built when a function is registered, so an unregistered class fails
# there. `validate()` catches the other direction: a port naming an identifier
# this registry can no longer resolve, which would otherwise surface as a failed
# expansion mid-assignment.


class ShelvedThing:
    """A plain class, kept on the agent rather than fetched."""


def test_validate_passes_when_every_port_resolves() -> None:
    app = AppRegistry()
    app.register_memory_structure(ShelvedThing)

    @app.register
    def uses_it(thing: ShelvedThing) -> int:
        """Take a thing."""
        return 1

    app.validate()


def test_validate_catches_a_port_naming_a_structure_the_registry_lost() -> None:
    app = AppRegistry()
    app.register_memory_structure(ShelvedThing)

    @app.register
    def uses_it(thing: ShelvedThing) -> int:
        """Take a thing."""
        return 1

    # As if the structure had been declared into a different registry, or its
    # client had been dropped from the app after the fact.
    app.structure_registry.identifier_memory_structure_map.clear()

    with pytest.raises(StructureRegistryError) as raised:
        app.validate()

    assert "uses_it" in str(raised.value) and "thing" in str(raised.value)
