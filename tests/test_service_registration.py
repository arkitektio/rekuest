"""A service is declared on the registry it ships with, beside its structures.

There is no process-wide catalog and no class marker: ``@registry.service()`` on
the builder makes what it returns *a client*, and an expander or an action asking
for that class by annotation is handed the built client. Another registry takes
it all in with ``register_service``.
"""

from typing import Annotated, Optional

import pytest
from fakts import Alias, Require

from rekuest.protocol.schema import PortKind
from rekuest.app import AppRegistry
from rekuest.definition.errors import DefinitionError
from rekuest.errors import RegistryFrozenError
from rekuest.service import Service
from rekuest.state.utils import prepare_injected_variables
from rekuest.structures.errors import StructureDefinitionError


class Thing:
    def __init__(self, id: str) -> None:
        self.id = id


class ThingClient:
    """The client the things service returns."""


class OtherClient:
    """Something no service returns."""


def things_registry() -> tuple[AppRegistry, Service[ThingClient]]:
    """A package's registry: its service, then the structure its client expands."""
    registry = AppRegistry()

    @registry.service()
    def things(things: Annotated[Alias, Require("live.test.things")]) -> ThingClient:
        """The things service."""
        return ThingClient()

    @registry.structure("@things/thing")
    async def expand_thing(id: str, things: ThingClient) -> Thing:
        """A thing, by id."""
        return Thing(id)

    return registry, things


def test_declaring_records_the_service_and_what_it_returns() -> None:
    registry, things = things_registry()

    assert registry.services == {"things": things}
    assert things.returns is ThingClient and things.registry is registry
    assert registry.structure_registry.clients == {"things": ThingClient}
    assert registry.structure_registry.is_client(ThingClient)
    assert registry.structure_registry.is_client(Optional[ThingClient])
    assert not registry.structure_registry.is_client(OtherClient)
    assert not registry.structure_registry.is_client(int)


def test_an_expander_may_ask_for_a_declared_client_only() -> None:
    registry, _ = things_registry()

    with pytest.raises(
        StructureDefinitionError, match="no registered service returns one"
    ):

        @registry.structure("@things/other")
        async def expand_other(id: str, other: OtherClient) -> Thing:
            return Thing(id)

    assert "@things/other" not in registry.structure_registry.identifier_structure_map


def test_register_service_takes_in_the_service_and_its_structures() -> None:
    package, things = things_registry()
    app = AppRegistry()

    app.register_service(things)

    assert app.services == {"things": things}
    assert app.structure_registry.clients == {"things": ThingClient}
    structure = app.structure_registry.identifier_structure_map["@things/thing"]
    assert structure.service == "things"
    assert package.services == {"things": things}, "the package registry is untouched"


def test_the_same_service_twice_is_a_no_op() -> None:
    _, things = things_registry()
    app = AppRegistry()
    app.register_service(things)
    app.register_service(things)

    assert list(app.services) == ["things"]


def test_a_second_service_of_one_name_is_refused() -> None:
    _, one = things_registry()
    _, two = things_registry()
    app = AppRegistry()
    app.register_service(one)

    with pytest.raises(ValueError, match="both named 'things'"):
        app.register_service(two)


def test_a_frozen_registry_refuses_a_service() -> None:
    _, things = things_registry()
    registry = AppRegistry()
    registry.freeze()

    with pytest.raises(RegistryFrozenError):
        registry.register_service(things)

    with pytest.raises(RegistryFrozenError):

        @registry.service()
        def late(late: Annotated[Alias, Require("live.test.late")]) -> OtherClient:
            return OtherClient()


def test_a_snapshot_keeps_the_services_and_their_clients() -> None:
    registry, things = things_registry()

    snapshot = registry.snapshot()

    assert snapshot.services == {"things": things}
    assert snapshot.structure_registry.clients == {"things": ThingClient}
    with pytest.raises(RegistryFrozenError):
        snapshot.register_service(things_registry()[1])


def test_an_action_asking_for_a_declared_client_is_handed_it_not_a_port() -> None:
    registry, _ = things_registry()

    @registry.register
    def count(x: int, things: ThingClient) -> int:
        """Counts, with the client."""
        return x

    definition = registry.implementations["count"].definition
    assert [port.key for port in definition.args] == ["x"]
    assert definition.args[0].kind == PortKind.INT
    injected = prepare_injected_variables(count, registry.structure_registry)
    assert injected.service_client_variables == {"things": ThingClient}


def test_an_action_asking_for_an_undeclared_client_fails_at_registration() -> None:
    registry = AppRegistry()

    # Not a client here, so it is a port -- and nothing registered it as one.
    with pytest.raises(DefinitionError, match="register the service"):

        @registry.register
        def count(x: int, things: ThingClient) -> int:
            """Asks for a client no service here returns."""
            return x
