
import pytest

from rekuest.agents.hooks.shutdown import shutdown
from rekuest.agents.hooks.startup import startup
from rekuest.state.decorator import state
from rekuest.app import AppRegistry

# Hooks and states belong to a registry, as they would to an app.
_REGISTRY = AppRegistry()
_HOOKS = _REGISTRY.hooks_registry
_STRUCTURES = _REGISTRY.structure_registry


def test_startup_decorator():
    @startup(registry=_HOOKS, structure_registry=_STRUCTURES)
    def my_startup_hook():
        pass

    @_REGISTRY.context
    class Hallo:
        pass

    @state(registry=_REGISTRY)
    class HalloState:
        pass

    @startup(registry=_HOOKS, structure_registry=_STRUCTURES)
    def my_startup_hook_returns_context() -> Hallo:
        return Hallo()
        pass

    @startup(registry=_HOOKS, structure_registry=_STRUCTURES)
    def my_startup_hook_returns_context_and_state() -> tuple[Hallo, HalloState]:
        return Hallo(), HalloState()
        pass


def test_shutdown_decorator():
    @_REGISTRY.context
    class Tschau:
        pass

    @state(registry=_REGISTRY)
    class TschauState:
        pass

    @shutdown(registry=_HOOKS, structure_registry=_STRUCTURES)
    def my_shutdown_hook():
        pass

    @shutdown(registry=_HOOKS, structure_registry=_STRUCTURES)
    async def my_async_shutdown_hook(tschau: Tschau, tschau_state: TschauState) -> None:
        pass

    with pytest.raises(ValueError):

        @shutdown(registry=_HOOKS, structure_registry=_STRUCTURES)
        def my_shutdown_hook_returns_state() -> TschauState:
            return TschauState()

    with pytest.raises(ValueError):

        @shutdown(registry=_HOOKS, structure_registry=_STRUCTURES)
        def my_shutdown_hook_with_unknown_arg(unknown: int) -> None:
            pass
