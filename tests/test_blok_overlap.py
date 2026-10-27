
from arkitekt_spec.actions import (
    ArgPortInput,
    ImplementationInput,
    PortKind,
    StateImplementationInput,
)
from arkitekt_spec.declare.definition.dependencies import (
    build_action_dependency_input,
    build_state_dependency_input,
)
from arkitekt_spec.declare.declare import DeclaredAgentAction, DeclaredAgentState
from arkitekt_spec.declare.definition.define import prepare_definition
from arkitekt_spec.declare.definition.match import build_port_match, build_port_matches
from arkitekt_spec.declare.app import AppRegistry


def test_build_port_match_preserves_nested_shape() -> None:
    port = ArgPortInput(
        key="items",
        kind=PortKind.LIST,
        nullable=False,
        children=(
            ArgPortInput(
                key="...",
                kind=PortKind.STRING,
                nullable=False,
            ),
        ),
    )

    match = build_port_match(2, port)

    assert match.at == 2
    assert match.key == "items"
    assert match.children is not None
    assert len(match.children) == 1
    assert match.children[0].at == 0
    assert match.children[0].key == "..."
    assert match.children[0].kind == PortKind.STRING


def test_build_port_matches_returns_none_for_empty_ports() -> None:
    assert build_port_matches(()) is None


def test_declared_action_and_blok_registry_build_same_action_dependency(
    simple_registry,
) -> None:
    class DemoProtocol:
        def run(self, item: str) -> str:
            return item

    declared = DeclaredAgentAction(DemoProtocol.run, "demo", "run", simple_registry)

    definition_registry = AppRegistry()
    definition_registry.register_at_interface(
        "run",
        ImplementationInput(
            definition=prepare_definition(
                DemoProtocol.run,
                structure_registry=simple_registry,
                omitfirst=1,
            ),
            dependencies=(),
            interface="run",
            needs_token=True,
        ),
        lambda *args, **kwargs: None,
    )

    declared_dependency = declared.to_dependency_input()
    # What a blok reading ``self.run`` demands (arkitekt_spec.declare.blok.registry).
    blok_dependency = build_action_dependency_input(
        key="run",
        definition=definition_registry.implementations["run"].definition,
        action_key="run",
    )

    assert declared_dependency.model_dump() == blok_dependency.model_dump()


def test_declared_state_and_blok_registry_build_same_state_dependency(
    simple_registry,
) -> None:
    class DemoState:
        item: str

    declared = DeclaredAgentState(DemoState, "demo", "status", simple_registry)

    state_registry = AppRegistry()
    state_registry.register_state(
        DemoState,
        StateImplementationInput(
            interface="status",
            definition=declared.definition,
        ),
        simple_registry,
    )

    declared_dependency = declared.to_dependency_input()
    # What a blok reading ``self.status`` demands (arkitekt_spec.declare.blok.registry).
    blok_dependency = build_state_dependency_input(
        key="status",
        state_key="status",
        definition=state_registry.states["status"].definition,
    )

    assert declared_dependency.model_dump() == blok_dependency.model_dump()
