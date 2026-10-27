"""The pre-upload check over a whole agent payload. No network: catalogs are passed in."""

import pytest

from arkitekt_spec.actions import (
    ActionKind,
    ArgPortInput,
    BlokImplementationInput,
    DefinitionInput,
    ImplementAgentInput,
    ImplementationInput,
    PortKind,
)
from arkitekt_spec.blok.catalogs import BlokView
from arkitekt_spec.blok.vocabulary import BlokError
from arkitekt_spec.declare.blok import bsx
from arkitekt_spec.declare.catalogs import UNKNOWN_CATALOG, UNKNOWN_OPERATION
from rekuest.client.catalogs import blok_view_for, validate_agent_input
from arkitekt_spec.declare.widgets import withValidator

from .catalog_cases import ELECTRON, NAMED_ONLY


def _agent(bloks: tuple = (), implementations: tuple = ()) -> ImplementAgentInput:
    return ImplementAgentInput(
        name="agent", states=(), implementations=implementations, bloks=bloks
    )


def _blok(component: str, module: str | None = None) -> BlokImplementationInput:
    return BlokImplementationInput(
        key="b", vocab=2, dependencies=(), source=component, module=module
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
    agent = _agent(bloks=(_blok('<Html><Box><Slidr min="1" /></Box></Html>', module="electron"),))
    [finding] = validate_agent_input(agent, {"electron": ELECTRON})
    assert finding.code == "catalog.unknown_component"
    assert "Slidr" in finding.message


def test_a_valid_blok_passes() -> None:
    agent = _agent(bloks=(_blok("<Html><Box><Card /></Box></Html>", module="electron"),))
    assert validate_agent_input(agent, {"electron": ELECTRON}) == []


def test_a_module_shadows_a_standard_component() -> None:
    """The module's ``Slider`` is drawn, and the tree that uses it is warned."""
    agent = _agent(bloks=(_blok("<Html><Box><Slider min={1} /></Box></Html>", module="electron"),))
    [finding] = validate_agent_input(agent, {"electron": ELECTRON})
    assert finding.code == "catalog.shadow"
    assert "electron" in finding.message


def test_a_blok_naming_no_module_is_the_orkestrators() -> None:
    """Base, the standard catalogs and the Orkestrator's module: a v2 blok's default."""
    agent = _agent(bloks=(_blok('<Html><Card><Slidr min={1} /></Card></Html>'),))
    [finding] = validate_agent_input(agent, {"electron": ELECTRON})
    assert finding.code == "catalog.unknown_component"
    assert validate_agent_input(_agent(bloks=(_blok("<Html><Card><Button>Go</Button></Card></Html>"),)), {}) == []


def test_a_named_module_joins_the_standard_catalogs() -> None:
    """Base, the standard catalogs and the Orkestrator's module always; the module's definition wins a shadowed name."""
    view: BlokView = blok_view_for("electron", {"electron": ELECTRON})
    assert view.component("Slider") == ELECTRON.components[0]
    assert view.component("Card") is not None
    assert view.component("foreach") is not None
    assert view.label.endswith("orkestrator + electron")
    assert view.shadows["Slider"].winner == "electron"


def test_a_blok_breaking_the_language_is_refused() -> None:
    agent = _agent(bloks=(_blok("<Html><Card><Text>{deps.robot.arm}</Text></Card></Html>"),))
    with pytest.raises(ValueError, match=r"Blok 'b'") as refused:
        validate_agent_input(agent, {})
    assert isinstance(refused.value, BlokError) and refused.value.code == "roots.not_in_kind"


def test_a_blok_that_does_not_parse_is_refused_with_its_code() -> None:
    agent = _agent(bloks=(_blok("<Html><Card><Button onClick={nosuch.thing()}>Go</Button></Card></Html>"),))
    with pytest.raises(ValueError, match=r"^Blok 'b': line 1, column \d+: .*\[expr\.unknown_callee\]$") as refused:
        validate_agent_input(agent, {})
    assert isinstance(refused.value, BlokError) and (refused.value.code, refused.value.line) == ("expr.unknown_callee", 1)


def test_a_blok_naming_an_unknown_module_is_warned() -> None:
    """Nothing checks its components, so the server warns ``module.unknown``, and so does this."""
    agent = _agent(bloks=(_blok("<Html><Anything /></Html>", module="not-on-the-server"),))
    [finding] = validate_agent_input(agent, {"electron": ELECTRON})
    assert finding.code == "module.unknown"
    assert "not-on-the-server" in finding.message and "electron, orkestrator" in finding.message


def test_a_blok_naming_the_orkestrator_is_not_warned() -> None:
    agent = _agent(bloks=(_blok("<Html><Card /></Html>", module="orkestrator"),))
    assert validate_agent_input(agent, {}) == []


def test_a_blok_against_a_name_only_module_checks_nothing_but_warns() -> None:
    """It registered nothing, so nothing is knowable -- the server's rule -- and it is unknown."""
    agent = _agent(bloks=(_blok("<Html><Slidr /></Html>", module="electron"),))
    assert [finding.code for finding in validate_agent_input(agent, {"electron": NAMED_ONLY})] == ["module.unknown"]


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
                vocab=2,
                components=(bsx("<Html><Box><Slidr /></Box></Html>"),),
                module="electron",
            ),
        ),
        implementations=(_implementation("nosuchop(value)", catalogs=("electron",)),),
    )
    findings = validate_agent_input(agent, {"electron": ELECTRON})
    assert [f.code for f in findings] == ["catalog.unknown_component", UNKNOWN_OPERATION]


def test_an_agent_blok_resolves_its_locals_against_its_own_actions() -> None:
    """Locals travel as written; the check resolves a left-out kind as the server does, from
    the agent's own arg ports: ``x`` passed whole to ``self.i(a: x)`` is the INT of port ``a``
    (a task argument beats a bind), so binding it to a Switch is refused (``local.bind_kind``);
    without the action the Switch alone makes it a BOOL."""
    from arkitekt_spec.actions import BlokLocalInput

    blok = BlokImplementationInput(
        key="b",
        vocab=2,
        dependencies=(),
        locals=(BlokLocalInput(key="x"),),
        source="<Html><Card><Switch bind={x} /><Button onClick={self.i(a: x)} /></Card></Html>",
    )
    with pytest.raises(BlokError, match=r"x is a INT local.*\[local.bind_kind\]"):
        validate_agent_input(_agent(bloks=(blok,), implementations=(_implementation("gt(value, 0)"),)), {})
    assert blok.locals is not None and "kind" not in blok.locals[0].model_fields_set

    unknown = blok.model_copy(update={"source": "<Html><Card><Switch bind={x} /><Button onClick={self.other(a: x)} /></Card></Html>"})
    assert validate_agent_input(_agent(bloks=(unknown,), implementations=(_implementation("gt(value, 0)"),)), {}) == []
