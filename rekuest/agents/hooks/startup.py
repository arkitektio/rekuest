"""Hooks for the agent"""

import inspect
from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
    cast,
    overload,
)
from collections.abc import Callable
from rekuest.agents.types import BoundApp
from koil.bridge import run_threaded
from rekuest.errors import NoRegistryError
from rekuest.agents.hooks.errors import StartupHookError
from rekuest.agents.hooks.registry import (
    HooksRegistry,
    StartupHookReturns,
)
from rekuest.agents.hooks.variables import WithVariables

if TYPE_CHECKING:
    from rekuest.structures.registry import StructureRegistry
from rekuest.protocols import (
    AnyFunction,
    AsyncStartupFunction,
    ContextLessStartupFunction,
    ThreadedStartupFunction,
    StartupFunction,
)
from rekuest.remote import ensure_return_as_tuple
from rekuest.state.utils import get_return_length


class StartupWithVariables(WithVariables):
    """Startup hooks run before any state or context exists: only the app context is injectable."""

    hook_kind = "Startup"
    injects_states = False

    def get_startup_kwargs(self, app_context: Any, bound_app: BoundApp | None) -> dict[str, Any]:  # noqa: ANN401
        """Bind the app context and the app by parameter name.

        By name rather than by position, so one hook can take both. No state or
        context exists yet, hence the empty mappings.
        """
        return self.get_kwargs({}, {}, app_context, bound_app=bound_app)

    def validate_returns(self, func: AnyFunction) -> None:
        allowed_return_types = self.state_returns.count + self.context_returns.count
        if get_return_length(inspect.signature(func)) > allowed_return_types:
            raise ValueError(
                f"Startup function {func.__name__} has more return values than the context and state variables. "
                f"Expected at most {allowed_return_types} return values, but got {get_return_length(inspect.signature(func))}."
            )


class WrappedStartupHook(StartupWithVariables):
    """Startup hook that runs in the event loop"""

    def __init__(
        self, func: AsyncStartupFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        """Initialize the startup hook

        Args:
            func (Callable[[str, Any], AnyContext]): The function to run in the startup hook
            func (Callable): The function to run in the startup hook
        """
        super().__init__(func, structure_registry)

    async def arun(
        self,
        app_context: Any,  # noqa: ANN401
        bound_app: BoundApp | None = None,
    ) -> StartupHookReturns:
        """Run the startup hook in the event loop
        Args:
            app_context (Any): The context for the startup hook
            bound_app (Any): The app the agent belongs to, if any
        Returns:
            Optional[Dict[str, Any]]: The state variables and contexts
        """
        parsed_returns = await self.func(**self.get_startup_kwargs(app_context, bound_app))

        returns = ensure_return_as_tuple(parsed_returns)

        states: dict[str, Any] = {}
        contexts: dict[str, Any] = {}

        for index, return_value in enumerate(returns):
            if index in self.state_returns.state_returns:
                states[self.state_returns.state_returns[index]] = return_value
            elif index in self.context_returns.context_returns:
                contexts[self.context_returns.context_returns[index]] = return_value
            else:
                raise StartupHookError(
                    f"Startup hook must return state or context variables. Other returns are not allowed {self.context_returns}, {self.state_returns}"
                )

        return StartupHookReturns(states=states, contexts=contexts)


class ThreadedStartupHook(StartupWithVariables):
    """Startup hook that runs in the event loop"""

    def __init__(
        self, func: ThreadedStartupFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        """Initialize the startup hook

        Args:
            func (Callable[[str], AnyContext]): The function to run in the startup hook
            func (Callable): The function to run in the startup hook
        """
        super().__init__(func, structure_registry)

    async def arun(
        self,
        app_context: Any,  # noqa: ANN401
        bound_app: BoundApp | None = None,
    ) -> StartupHookReturns:
        """Run the startup hook in the event loop
        Args:
            app_context (Any): The context for the startup hook
            bound_app (Any): The app the agent belongs to, if any
        Returns:
            Optional[Dict[str, Any]]: The state variables and contexts
        """

        parsed_returns = await run_threaded(
            self.func,
            **self.get_startup_kwargs(app_context, bound_app),  # type: ignore[arg-type]
        )

        returns = ensure_return_as_tuple(parsed_returns)

        states: dict[str, Any] = {}
        contexts: dict[str, Any] = {}

        for index, return_value in enumerate(returns):
            if index in self.state_returns.state_returns:
                states[self.state_returns.state_returns[index]] = return_value
            elif index in self.context_returns.context_returns:
                contexts[self.context_returns.context_returns[index]] = return_value
            else:
                raise StartupHookError(
                    "Startup hook must return state or context variables. Other returns are not allowed"
                )

        return StartupHookReturns(states=states, contexts=contexts)


TStartup = TypeVar("TStartup", bound=StartupFunction | ContextLessStartupFunction)


@overload
def startup(*args: TStartup) -> TStartup:
    """Decorator to register a startup hook"""

    ...


@overload
def startup(
    *,
    name: str | None = None,
    registry: HooksRegistry | None = None,
    structure_registry: "StructureRegistry | None" = None,
) -> Callable[[TStartup], TStartup]:
    """Decorator to register a startup hook

    Args:
        name (str): The name of the startup hook. If not provided, the function name will be used.
        registry (HooksRegistry): The registry to register into. Required.
    """
    ...


@overload
def startup(
    *args: TStartup,
    name: str | None = None,
    registry: HooksRegistry | None = None,
    structure_registry: "StructureRegistry | None" = None,
) -> TStartup | Callable[[TStartup], TStartup]:
    """Decorator to register a startup hook"""


# --- Implementation ---
def startup(
    *args: TStartup,
    name: str | None = None,
    registry: HooksRegistry | None = None,
    structure_registry: "StructureRegistry | None" = None,
) -> TStartup | Callable[[TStartup], TStartup]:
    """Register a startup hook on the selected hook registry.

    Startup hooks run when the agent boots. Their signatures are inspected for
    app-context, state, and context dependencies, and their return annotations
    are used to decide which state or context objects should be published for
    the rest of the agent lifecycle.

    Async startup hooks run directly in the event loop. Synchronous startup
    hooks are wrapped in ``ThreadedStartupHook`` and executed through
    ``run_threaded`` so they do not block the loop.

    Args:
        *args: Startup function to register when used as ``@startup`` without
            parentheses.
        name: Explicit registry key. Defaults to the function name.
        registry: Hook registry to populate. Required: without one this raises
            ``NoRegistryError``, since hooks go through an app (``@app.startup``).

    Returns:
        The original function, or a decorator configured with the provided
        metadata.

    Raises:
        ValueError: If more than one function is passed at once.

    Reached through ``AppRegistry.startup``; call it directly only with
    ``registry=``.

    Examples:
        Register an async startup hook that returns initial state::

            @app.startup
            async def boot(app_context: MyAppContext) -> MyState:
                return MyState(counter=0)
    """

    if len(args) > 1:
        raise ValueError("You can only register one function at a time.")

    if len(args) == 1:
        func = args[0]
        if registry is None:
            raise NoRegistryError.for_decorator(
                "Hooks go", "startup", "async def my_hook(): ...", "registry"
            )

        if inspect.iscoroutinefunction(func):
            a = cast(AsyncStartupFunction, func)
            registry.register_startup(name or a.__name__, WrappedStartupHook(a, structure_registry))

        else:
            assert inspect.isfunction(func) or inspect.ismethod(func), (
                "Function must be a async function or a sync function"
            )
            t = cast(ThreadedStartupFunction, func)

            registry.register_startup(name or t.__name__, ThreadedStartupHook(t, structure_registry))

        return cast(TStartup, func)
    else:

        def decorator(func: TStartup) -> TStartup:
            return cast(TStartup, startup(func, name=name, registry=registry, structure_registry=structure_registry))

        return decorator
