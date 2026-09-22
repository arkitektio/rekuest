"""Hooks for the agent"""

import inspect
from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
    cast,
    get_type_hints,
    overload,
)
from collections.abc import Callable
import asyncio

from rekuest.agents.types import BoundApp
from koil.bridge import run_threaded
from rekuest.state.publish import StateHolder
from rekuest.agents.hooks.registry import (
    HooksRegistry,
)
from rekuest.agents.hooks.variables import WithVariables

if TYPE_CHECKING:
    from rekuest.structures.registry import StructureRegistry
from rekuest.protocol.types import (
    AnyFunction,
    AsyncShutdownFunction,
    ShutdownFunction,
    ThreadedShutdownFunction,
)
from rekuest.definition.define import is_none_type
from rekuest.state.utils import is_empty_type


class ShutdownWithVariables(WithVariables):
    """Shutdown hooks may take anything but must not return: the agent is tearing down."""

    hook_kind = "Shutdown"

    def validate_returns(self, func: AnyFunction) -> None:
        # Resolve the hints first: an unresolved ``-> None`` annotation is the literal
        # None, which get_return_length would count as a return value.
        try:
            hints = get_type_hints(func, include_extras=True)
        except Exception:
            hints = {}
        returns = hints.get("return", inspect.signature(func).return_annotation)

        if not (is_none_type(returns) or is_empty_type(returns)):
            raise ValueError(
                f"Shutdown function {func.__name__} must not return anything, but returns {returns}. "
                "The agent is tearing down, so returned states and contexts would never be used."
            )


class WrappedShutdownHook(ShutdownWithVariables):
    """Shutdown hook that runs in the event loop"""

    def __init__(
        self, func: AsyncShutdownFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        """Initialize the shutdown hook

        Args:
            func (Callable): The function to run when the agent tears down
        """
        super().__init__(func, structure_registry)

    async def arun(
        self,
        agent: StateHolder,
        contexts: dict[str, Any],
        states: dict[str, Any],
        app_context: Any,
        bound_app: BoundApp | None = None,
    ) -> None:
        """Run the shutdown hook in the event loop"""
        kwargs = self.get_kwargs(contexts, states, app_context, bound_app=bound_app)
        await self.func(**kwargs)


class ThreadedShutdownHook(ShutdownWithVariables):
    """Shutdown hook that runs in a thread"""

    def __init__(
        self, func: ThreadedShutdownFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        """Initialize the shutdown hook

        Args:
            func (Callable): The function to run when the agent tears down
        """
        super().__init__(func, structure_registry)

    def run_with_publishing(self, agent: StateHolder, **kwargs: Any) -> None:
        self.func(**kwargs)

    async def arun(
        self,
        agent: StateHolder,
        contexts: dict[str, Any],
        states: dict[str, Any],
        app_context: Any,
        bound_app: BoundApp | None = None,
    ) -> None:
        """Run the shutdown hook in a thread"""
        kwargs = self.get_kwargs(contexts, states, app_context, bound_app=bound_app)
        await run_threaded(
            self.run_with_publishing,
            agent,
            **kwargs,  # type: ignore[arg-type]
        )


TShutdown = TypeVar("TShutdown", bound=ShutdownFunction)


@overload
def declare_shutdown(
    func: TShutdown,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> TShutdown: ...


@overload
def declare_shutdown(
    func: None = None,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> Callable[[TShutdown], TShutdown]: ...


# --- Implementation ---
def declare_shutdown(
    func: TShutdown | None = None,
    /,
    *,
    name: str | None = None,
    registry: HooksRegistry,
    structure_registry: "StructureRegistry | None" = None,
) -> TShutdown | Callable[[TShutdown], TShutdown]:
    """Register a shutdown hook on the selected hook registry.

    Shutdown hooks run when the agent tears down, in the reverse of the order they
    were registered, and are the counterpart of ``startup`` hooks: they release
    whatever the app acquired while it was running. They run on every teardown,
    including the ones caused by an error or a cancellation, but only if the agent
    got far enough to run its startup hooks.

    The signature is inspected for state, context, and app-context dependencies so
    the runtime can inject the live values the agent is about to drop. A shutdown
    hook must not return anything.

    Async shutdown hooks run directly in the event loop. Synchronous shutdown hooks
    are wrapped in ``ThreadedShutdownHook`` and executed through ``run_threaded`` so
    they do not block the loop. State changes they make publish to the agent that
    adopted the state, as changes made outside a task.

    A hook that raises is logged and the remaining hooks still run: teardown never
    fails because of a shutdown hook.

    Args:
        *args: Shutdown function to register when used as ``@shutdown`` without
            parentheses.
        name: Explicit registry key. Defaults to the function name.
        registry: The hook registry the hook is recorded in.

    Returns:
        The original function, or a decorator configured with the provided
        metadata.

    Raises:
        ValueError: If more than one function is passed at once.

    Reached through ``AppRegistry.shutdown``; call it directly only with
    ``registry=``.

    Examples:
        Close a client that a startup hook put on a context::

            @app.shutdown
            async def teardown(my_context: MyContext) -> None:
                await my_context.client.aclose()
    """

    def decorator(function: TShutdown) -> TShutdown:
        if asyncio.iscoroutinefunction(function):
            a = cast(AsyncShutdownFunction, function)
            registry.register_shutdown(name or a.__name__, WrappedShutdownHook(a, structure_registry))

        else:
            assert inspect.isfunction(function) or inspect.ismethod(function), (
                "Function must be a async function or a sync function"
            )
            t = cast(ThreadedShutdownFunction, function)

            registry.register_shutdown(name or t.__name__, ThreadedShutdownHook(t, structure_registry))

        return cast(TShutdown, function)

    return decorator(func) if func is not None else decorator
