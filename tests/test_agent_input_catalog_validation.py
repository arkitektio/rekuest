"""The pre-upload check over a whole agent payload. No network: catalogs are passed in."""

import pytest

from rekuest.api.schema import (
    ActionKind,
    ArgPortInput,
    BlokImplementationInput,
    DefinitionInput,
    ImplementAgentInput,
    ImplementationInput,
    PortKind,
)
from rekuest.blok import bsx
from rekuest.catalogs import UNKNOWN_CATALOG, UNKNOWN_OPERATION
from rekuest.catalogs.remote import DEFAULT_CATALOG_NAME, validate_agent_input
from rekuest.widgets import withValidator

from .catalog_cases import BOX, ELECTRON, NAMED_ONLY, SLIDER


def _agent(bloks: tuple = (), implementations: tuple = ()) -> ImplementAgentInput:
    return ImplementAgentInput(
        name="agent", states=(), implementations=implementations, bloks=bloks
    )


def _blok(component: str, catalog: str | None = None) -> BlokImplementationInput:
    return BlokImplementationInput(
        key="b", dependencies=(), components=(bsx(component),), catalog=catalog
    )


def _implementation(expression: str, catalogs: tuple = ()) -> ImplementationInput:
    definition = DefinitionInput(
        key="k",
        name="n",
        version="1",
        kind=ActionKind.FUNCTION,
        args=(
            ArgPortInput(
                key="a",
                kind=PortKind.INT,
                nullable=False,
                validators=(withValidator(expression),),
            ),
        ),
        returns=(),
        catalogs=catalogs,
    )
    return ImplementationInput(definition=definition, interface="i", dependencies=())


def test_a_blok_is_checked_against_the_catalog_it_names() -> None:
    agent = _agent(bloks=(_blok('<Box><Slidr min="1" /></Box>', catalog="electron"),))
    with pytest.raises(ValueError, match="is not registered in catalog"):
        validate_agent_input(agent, {"electron": ELECTRON})


def test_a_valid_blok_passes() -> None:
    agent = _agent(bloks=(_blok('<Box><Slider min="1" /></Box>', catalog="electron"),))
    assert validate_agent_input(agent, {"electron": ELECTRON}) == []


def test_a_blok_naming_no_catalog_uses_the_default_one() -> None:
    """The server creates a catalog called 'default' for a blok that names none."""
    agent = _agent(bloks=(_blok('<Box><Slidr min="1" /></Box>'),))
    catalogs = {DEFAULT_CATALOG_NAME: ELECTRON}
    with pytest.raises(ValueError, match="is not registered in catalog"):
        validate_agent_input(agent, catalogs)


def test_a_blok_never_warns_about_an_unknown_catalog() -> None:
    """The server creates whatever the blok names, so this is not a finding."""
    agent = _agent(bloks=(_blok("<Anything />", catalog="not-on-the-server"),))
    assert validate_agent_input(agent, {}) == []


def test_a_blok_against_a_name_only_catalog_checks_nothing() -> None:
    """It registered no components, so nothing is knowable -- the server's rule."""
    agent = _agent(bloks=(_blok("<Slidr />", catalog="electron"),))
    assert validate_agent_input(agent, {"electron": NAMED_ONLY}) == []


def test_a_definition_warns_about_an_unknown_catalog() -> None:
    """Definitions carry a catalog list and do get the finding bloks never get."""
    agent = _agent(implementations=(_implementation("value > 1", catalogs=("nope",)),))
    findings = validate_agent_input(agent, {"electron": ELECTRON})
    assert [f.code for f in findings] == [UNKNOWN_CATALOG]
    assert "nope" in findings[0].message


def test_base_is_accepted_as_a_catalog_name() -> None:
    agent = _agent(implementations=(_implementation("value > 1", catalogs=("base",)),))
    assert validate_agent_input(agent, {}) == []


def test_another_base_version_warns() -> None:
    agent = _agent(implementations=(_implementation("value > 1", catalogs=("base@2",)),))
    findings = validate_agent_input(agent, {})
    assert [f.code for f in findings] == [UNKNOWN_CATALOG]


def test_a_definition_operation_is_checked_against_its_catalogs() -> None:
    agent = _agent(
        implementations=(
            _implementation("clamp(value=value, nope=1)", catalogs=("electron",)),
        )
    )
    with pytest.raises(ValueError, match="does not accept arguments"):
        validate_agent_input(agent, {"electron": ELECTRON})


def test_an_unknown_definition_operation_warns() -> None:
    agent = _agent(
        implementations=(_implementation("nosuchop(value)", catalogs=("electron",)),)
    )
    findings = validate_agent_input(agent, {"electron": ELECTRON})
    assert [f.code for f in findings] == [UNKNOWN_OPERATION]


def test_findings_come_back_bloks_first() -> None:
    agent = _agent(
        bloks=(
            BlokImplementationInput(
                key="b",
                dependencies=(),
                components=(bsx('<Box><Slider min="@utils.nosuchop(1)" /></Box>'),),
                catalog="electron",
            ),
        ),
        implementations=(_implementation("nosuchop(value)", catalogs=("electron",)),),
    )
    findings = validate_agent_input(agent, {"electron": ELECTRON})
    assert len(findings) == 2
    assert all(f.code == UNKNOWN_OPERATION for f in findings)
