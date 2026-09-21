"""Tests for ``@registry.service()``: a service declared by its signature.

A builder is handed the *resolved* address of each thing it requires, plus a way
to get a token -- never the fakts client. So the requirements, the addresses the
builder dials, and what the manifest carries are one declaration and cannot
drift. What it returns is the client, and from then on a parameter annotated
with that class is handed it.
"""

from typing import Annotated, Any, Optional

import pytest
from fakts import Alias, Fakts, Own, Require, TokenLoader
from fakts.testing import build_testing_fakts

from rekuest.app import AppRegistry
from rekuest.service import Service, ServiceDefinitionError


class Thing:
    """A client, for tests."""

    def __init__(self, **parts: Any) -> None:
        self.parts = parts


class Picture:
    def __init__(self, id: str) -> None:
        self.id = id


def test_the_signature_is_the_declaration() -> None:
    registry = AppRegistry()

    @registry.service()
    def thing(
        thing: Annotated[Alias, Require("live.test.thing", "The thing")],
        s3: Annotated[Optional[Alias], Require("live.test.s3", optional=True)],
    ) -> Thing:
        """A thing."""
        return Thing()

    assert isinstance(thing, Service)
    assert thing.name == "thing"
    assert thing.returns is Thing
    assert thing.registry is registry
    keys = {r.key: r for r in thing.get_requirements()}
    assert set(keys) == {"thing", "s3"}
    assert keys["thing"].service == "live.test.thing"
    assert keys["thing"].description == "The thing"
    assert keys["s3"].optional is True


@pytest.mark.asyncio
async def test_a_builder_is_handed_the_resolved_address_it_declared() -> None:
    """The address a builder dials is the one it declared -- that is the point.

    ``dokuments`` used to resolve a ``"datalayer"`` group it never declared, so
    the alias was never provisioned. Here the two cannot differ.
    """

    @AppRegistry().service()
    def thing(
        thing: Annotated[Alias, Require("live.test.thing")],
        s3: Annotated[Alias, Require("live.test.s3")],
    ) -> Thing:
        """A thing."""
        return Thing(thing=thing, s3=s3)

    async with build_testing_fakts(
        aliases={"thing": "http://thing-server", "s3": "http://store"}
    ) as fakts:
        built = await thing.build(fakts, AppRegistry())

    assert built.parts["thing"].to_http_path("graphql") == "http://thing-server/graphql"
    assert built.parts["s3"].to_http_path() == "http://store"
    assert all(isinstance(alias, Alias) for alias in built.parts.values())


@pytest.mark.asyncio
async def test_an_unreachable_required_service_fails_while_connecting() -> None:
    """The behaviour this design buys: loud at connect, not on first use."""
    from fakts.errors import AliasNotFoundError

    @AppRegistry().service()
    def thing(thing: Annotated[Alias, Require("live.test.thing")]) -> Thing:
        """A thing."""
        return Thing()

    async with build_testing_fakts(aliases={}) as fakts:
        with pytest.raises(AliasNotFoundError):
            await thing.build(fakts, AppRegistry())


@pytest.mark.asyncio
async def test_an_optional_service_that_is_missing_yields_none() -> None:
    """kraph's datalayer: the client still builds, only uploads stop working."""

    @AppRegistry().service()
    def thing(
        thing: Annotated[Alias, Require("live.test.thing")],
        store: Annotated[Optional[Alias], Require("live.test.s3", optional=True)],
    ) -> Thing:
        """A thing."""
        return Thing(thing=thing, store=store)

    from fakts.models import Requirement

    fakts = build_testing_fakts(aliases={"thing": "http://thing-server"})
    # Declared by the app, but the deployment granted no instance for it.
    fakts.manifest.requirements.append(
        Requirement(key="store", service="live.test.s3", optional=True)
    )
    async with fakts:
        built = await thing.build(fakts, AppRegistry())

    assert built.parts["store"] is None
    assert built.parts["thing"] is not None


@pytest.mark.asyncio
async def test_a_builder_needs_no_fakts_at_all() -> None:
    """The point of narrowing: a literal address and a two-line loader suffice."""

    class StubLoader:
        async def aget_token(self) -> str:
            return "token"

        async def arefresh_token(self, stale_token: Optional[str] = None) -> str:
            return "token"

    @AppRegistry().service()
    def thing(
        thing: Annotated[Alias, Require("live.test.thing")],
        tokens: TokenLoader,
    ) -> Thing:
        """A thing."""
        return Thing(thing=thing, tokens=tokens)

    built = thing._function(  # the builder itself, with nothing injected for it
        thing=Alias(id="t", host="thing-server"), tokens=StubLoader()
    )
    assert built.parts["thing"].to_http_path("graphql") == "http://thing-server/graphql"


@pytest.mark.asyncio
async def test_the_own_alias_needs_no_requirement() -> None:
    """unlok's shape: the app's own server is not something to provision."""

    @AppRegistry().service()
    def nothing(own: Annotated[Alias, Own()], tokens: TokenLoader) -> Thing:
        """Built entirely on the app's own server."""
        return Thing(own=own)

    assert nothing.get_requirements() == []

    async with build_testing_fakts(aliases={}) as fakts:
        built = await nothing.build(fakts, AppRegistry())
        assert built.parts["own"] == await fakts.aget_self_alias()


@pytest.mark.asyncio
async def test_the_whole_fakts_and_the_registry_stay_injectable() -> None:
    @AppRegistry().service()
    def thing(fakts: Fakts, registry: AppRegistry) -> Thing:
        """Needs the app-wide parts -- rekuest's shape."""
        return Thing(fakts=fakts, registry=registry)

    fakts, registry = build_testing_fakts(aliases={}), AppRegistry()
    built = await thing.build(fakts, registry)
    assert built.parts["fakts"] is fakts
    assert built.parts["registry"] is registry
    assert thing.get_requirements() == []


def test_what_a_service_brings_is_the_registry_it_was_declared_on() -> None:
    """Another registry taking the service in gets its structures, stamped with its name."""
    brought = AppRegistry()

    @brought.service()
    def thing(thing: Annotated[Alias, Require("live.test.thing")]) -> Thing:
        """A thing that brings something."""
        return Thing()

    @brought.structure("@pictures/picture")
    async def expand_picture(id: str, thing: Thing) -> Picture:
        """A picture, by id."""
        return Picture(id)

    assert thing.registry is brought

    app = AppRegistry()
    app.register_service(thing)
    structure = app.structure_registry.identifier_structure_map["@pictures/picture"]
    assert structure.service == "thing"
    assert app.structure_registry.clients == {"thing": Thing}


def test_declaring_makes_what_it_returns_a_client_here_and_nowhere_else() -> None:
    class Fresh:
        """A client no service returned before."""

    here, elsewhere = AppRegistry(), AppRegistry()
    assert not here.structure_registry.is_client(Fresh)

    @here.service()
    def fresh(thing: Annotated[Alias, Require("live.test.thing")]) -> Fresh:
        """Returns a Fresh."""
        return Fresh()

    assert here.structure_registry.is_client(Fresh)
    assert not elsewhere.structure_registry.is_client(Fresh)
    assert elsewhere.services == {}, "declaring put it in no other registry"


# ------------------------------------------------------------------ #
# What a bad declaration says                                        #
# ------------------------------------------------------------------ #


def test_an_alias_without_a_require_is_refused() -> None:
    with pytest.raises(ServiceDefinitionError, match="which service fills it"):

        @AppRegistry().service()
        def thing(thing: Annotated[Alias, "not a marker"]) -> Thing:
            """No marker."""
            return Thing()


def test_a_bare_alias_is_refused() -> None:
    with pytest.raises(ServiceDefinitionError, match="bare Alias"):

        @AppRegistry().service()
        def thing(thing: Alias) -> Thing:
            """No marker at all."""
            return Thing()


def test_an_optional_require_must_say_so_in_the_type() -> None:
    with pytest.raises(ServiceDefinitionError, match="Say so in the type"):

        @AppRegistry().service()
        def thing(
            thing: Annotated[Alias, Require("live.test.thing", optional=True)],
        ) -> Thing:
            """Optional, but typed as always present."""
            return Thing()


def test_an_optional_type_must_say_so_in_the_require() -> None:
    with pytest.raises(ServiceDefinitionError, match="but requires it"):

        @AppRegistry().service()
        def thing(
            thing: Annotated[Optional[Alias], Require("live.test.thing")],
        ) -> Thing:
            """Typed optional, but required."""
            return Thing()


def test_a_missing_return_annotation_is_refused() -> None:
    with pytest.raises(ServiceDefinitionError, match="does not say what it returns"):

        @AppRegistry().service()
        def thing(thing: Annotated[Alias, Require("live.test.thing")]):  # noqa: ANN202
            """No return annotation."""
            return Thing()


def test_an_unannotated_parameter_is_refused() -> None:
    with pytest.raises(ServiceDefinitionError, match="unannotated parameter 'extra'"):

        @AppRegistry().service()
        def thing(thing: Annotated[Alias, Require("live.test.thing")], extra) -> Thing:  # noqa: ANN001
            """One parameter says nothing."""
            return Thing()


def test_a_parameter_a_run_cannot_supply_is_refused() -> None:
    with pytest.raises(ServiceDefinitionError, match="which a run cannot supply"):

        @AppRegistry().service()
        def thing(count: int) -> Thing:
            """Nothing fills an int."""
            return Thing()


def test_two_services_of_one_name_are_refused_by_a_registry() -> None:
    def declare() -> Service[Thing]:
        @AppRegistry().service()
        def thing(thing: Annotated[Alias, Require("live.test.thing")]) -> Thing:
            """A thing."""
            return Thing()

        return thing

    one, two = declare(), declare()
    app = AppRegistry()
    app.register_service(one)
    app.register_service(one)  # the same service again is nothing
    with pytest.raises(ValueError, match="both named 'thing'"):
        app.register_service(two)
