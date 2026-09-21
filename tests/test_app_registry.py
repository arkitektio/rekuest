"""A per-app registry: it holds exactly what its own app declared.

There is no registry above it, and nothing arrives by itself. What a service
brings goes in when the app takes the service (``register_service``), anything
else has to be registered here by hand, and a structure of a service the app
does not have is refused where it is named.
"""

from enum import Enum

import pytest

from rekuest.api.schema import Implementation, PortKind
from rekuest.app import AppRegistry
from rekuest.arkitekt import rekuest_service
from rekuest.definition.errors import DefinitionError
from rekuest.structures.errors import StructureRegistryError
from rekuest.structures.types import service_from_identifier
from rekuest.structures.utils import id_shrink

from .service_helpers import with_client


class LibraryThing:
    """Stands in for an SDK model such as mikro's ArrayDataset."""

    def __init__(self, id: str) -> None:
        self.id = id


class LibraryClient:
    """The client the lib service returns; its structures are fetched through it."""


class AppLocalThing:
    """A class only one app knows about."""


class Colour(Enum):
    RED = "red"
    BLUE = "blue"


def lib_registry() -> AppRegistry:
    """What the lib package ships: its service, then the structure it can fetch."""
    registry = AppRegistry()
    with_client(registry, LibraryClient, "lib")

    @registry.structure("@lib/thing")
    async def expand_thing(id: str, lib: LibraryClient) -> LibraryThing:
        """A thing, by id."""
        return LibraryThing(id)

    return registry


def app_with_lib() -> AppRegistry:
    """An app that takes the lib service, as ``App(services=[lib_service])`` does."""
    registry = AppRegistry()
    registry.register_service(lib_registry().services["lib"])
    return registry


# --------------------------------------------------------------------------- #
# What an app's registry holds
# --------------------------------------------------------------------------- #


def test_a_declared_structure_is_a_structure_not_a_memory_one() -> None:
    app = app_with_lib()

    port = app.structure_registry.get_argport_for_cls(LibraryThing, "x")

    assert port.kind == PortKind.STRUCTURE
    assert port.identifier == "@lib/thing"
    structure = app.structure_registry.get_fullfilled_structure("@lib/thing")
    assert structure.service == "lib" and structure.declared


def test_without_the_service_the_class_does_not_become_that_structure() -> None:
    """It used to be inherited from a process-wide registry. There is none now.

    ``LibraryThing`` cannot fetch itself, so with no service to declare it there
    is nothing this app could do with it -- and rather than quietly shelving it,
    which would put it on the wire as anything but ``@lib/thing``, it refuses.
    """
    app = AppRegistry()

    with pytest.raises(StructureRegistryError, match="not registered"):
        app.structure_registry.get_argport_for_cls(LibraryThing, "x")

    # Kept on this agent only if that is actually what was meant.
    app.register_memory_structure(LibraryThing)
    port = app.structure_registry.get_argport_for_cls(LibraryThing, "x")
    assert port.kind == PortKind.MEMORY_STRUCTURE
    assert port.identifier != "@lib/thing"


def test_two_apps_do_not_see_each_others_structures() -> None:
    a, b = app_with_lib(), AppRegistry()

    assert a.structure_registry.get_fullfilled_structure("@lib/thing")
    with pytest.raises(KeyError):
        b.structure_registry.get_fullfilled_structure("@lib/thing")
    assert "lib" not in b.services


def test_locally_registered_types_stay_in_the_app_that_holds_them() -> None:
    a, b = app_with_lib(), app_with_lib()

    a.register_memory_structure(AppLocalThing)
    a.structure_registry.get_argport_for_cls(AppLocalThing, "x")
    # An enum still derives on demand, so it lands in whichever app met it.
    a.structure_registry.get_argport_for_cls(Colour, "c")

    assert AppLocalThing in a.structure_registry.cls_fullfilled_type_map
    assert Colour in a.structure_registry.cls_fullfilled_type_map
    assert b.structure_registry.find_for_cls(AppLocalThing) is None
    assert b.structure_registry.find_for_cls(Colour) is None


def test_a_function_naming_a_missing_services_structure_fails_at_registration() -> None:
    """A generated, fetchable class with no service to name it."""
    app = AppRegistry()

    with pytest.raises(DefinitionError) as raised:

        @app.register
        def needs_rekuest(implementation: Implementation) -> None:
            """Takes a structure no service of this app declared."""

    assert "rekuest" in str(raised.value)
    assert app.is_empty()


def test_once_the_app_takes_the_service_the_function_registers() -> None:
    app = AppRegistry()
    app.register_service(rekuest_service)

    @app.register
    def takes_implementation(implementation: Implementation) -> None:
        """Takes a structure this app's rekuest service brought."""

    assert "takes_implementation" in app.implementations
    assert list(app.services) == ["rekuest"]


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


def test_service_is_derived_from_the_identifier() -> None:
    assert service_from_identifier("@mikro/arraydataset") == "mikro"
    assert service_from_identifier("@rekuest/action") == "rekuest"
    assert service_from_identifier("plain") is None
    assert service_from_identifier("@nothing") is None


def test_a_hand_registered_structure_never_asks_for_a_service() -> None:
    """Its service is guessed from the identifier, and `@myapp/handmade` names none."""

    class HandMade:
        """A structure registered directly on an app's registry."""

    async def aget(id: str) -> HandMade:
        return HandMade()

    app = AppRegistry()
    structure = app.structure_registry.register_as_structure(
        HandMade, "@myapp/handmade", aexpand=aget, ashrink=id_shrink
    )
    assert structure.service == "myapp" and not structure.declared

    @app.register
    def uses_my_own(thing: HandMade) -> None:
        """Takes a structure registered by hand."""

    assert app.required_services() == {}


def test_a_structure_a_service_brought_is_asked_for() -> None:
    """The control: this is what the missing-service warning is for."""
    app = AppRegistry()
    app.register_service(rekuest_service)

    @app.register
    def uses_rekuest(implementation: Implementation) -> None:
        """Takes a structure the rekuest service brought."""

    assert app.required_services() == {"rekuest": {"uses_rekuest"}}


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
    # service had been dropped from `services=[...]` after the fact.
    app.structure_registry.identifier_memory_structure_map.clear()

    with pytest.raises(StructureRegistryError) as raised:
        app.validate()

    assert "uses_it" in str(raised.value) and "thing" in str(raised.value)


def test_validate_walks_blok_dependencies_on_another_apps_actions() -> None:
    """The likeliest ports to drift: types this app does not implement itself.

    They are built against this registry at `register_blok`, so a structure that
    disappears from it afterwards leaves them naming nothing.
    """
    from typing import Protocol

    app = AppRegistry()
    app.register_memory_structure(ShelvedThing)

    @app.declare(app="other")
    class Remote(Protocol):
        def make(self, n: int) -> ShelvedThing:
            """Make a thing on another app."""
            ...

    @app.register
    def local(n: int) -> int:
        """Local."""
        return n

    app.register_blok(
        name="b",
        component="<Action key='local' />",
        dependencies={"dep": Remote},
    )
    app.validate()

    app.structure_registry.identifier_memory_structure_map.clear()

    with pytest.raises(StructureRegistryError, match="blok 'b'"):
        app.validate()
