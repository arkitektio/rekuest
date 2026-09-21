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
import asyncio

from rekuest.agents.types import BoundApp
from koil.bridge import run_threaded
from rekuest.errors import NoRegistryError
from rekuest.state.publish import StateHolder
from rekuest.agents.hooks.registry import (
    HooksRegistry,
)
from rekuest.agents.hooks.variables import WithVariables

if TYPE_CHECKING:
    from rekuest.structures.registry import StructureRegistry
from rekuest.protocols import (
    BackgroundFunction,
    ThreadedBackgroundFunction,
    AsyncBackgroundFunction,
)


class BackgroundWithVariables(WithVariables):
    hook_kind = "Background"


class WrappedBackgroundTask(BackgroundWithVariables):
    """Background task that runs in the event loop"""

    def __init__(
        self, func: AsyncBackgroundFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        """Initialize the background task
        Args:
            func (Callable): The function to run in the background async
        """
        super().__init__(func, structure_registry)

    async def arun(
        self,
        agent: StateHolder,
        contexts: dict[str, Any],
        states: dict[str, Any],
        app_context: Any = None,  # noqa: ANN401
        bound_app: BoundApp | None = None,
    ) -> None:
        """Run the background task in the event loop"""
        kwargs = self.get_kwargs(contexts, states, app_context, bound_app=bound_app)
        return await self.func(**kwargs)


class WrappedThreadedBackgroundTask(BackgroundWithVariables):
    """Background task that runs in a thread pool"""

    def __init__(
        self, func: ThreadedBackgroundFunction, structure_registry: "StructureRegistry | None" = None
    ) -> None:
        """Initialize the background task
        Args:
            func (Callable): The function to run in the background
        """
        super().__init__(func, structure_registry)

    def run_with_publishing(self, agent: StateHolder, **kwargs: Any) -> None:
        return self.func(**kwargs)

    async def arun(
        self,
        agent: StateHolder,
        contexts: dict[str, Any],
        states: dict[str, Any],
        app_context: Any = None,  # noqa: ANN401
        bound_app: BoundApp | None = None,
    ) -> None:
        """Run the background task in a thread pool"""
        kwargs = self.get_kwargs(contexts, states, app_context, bound_app=bound_app)
        return await run_threaded(
            self.run_with_publishing,
            agent,
            **kwargs,  # type: ignore[arg-type]
        )


TBackground = TypeVar("TBackground", bound=BackgroundFunction)


@overload
def background(*args: TBackground) -> TBackground: ...


@overload
def background(
    *,
    name: str | None = None,
    registry: HooksRegistry | None = None,
    structure_registry: "StructureRegistry | None" = None,
) -> Callable[[TBackground], TBackground]: ...


@overload
def background(
    *args: TBackground,
    name: str | None = None,
    registry: HooksRegistry | None = None,
    structure_registry: "StructureRegistry | None" = None,
) -> TBackground | Callable[[TBackground], TBackground]: ...


def background(  # noqa: ANN201
    *args: TBackground,
    name: str | None = None,
    registry: HooksRegistry | None = None,
    structure_registry: "StructureRegistry | None" = None,
) -> TBackground | Callable[[TBackground], TBackground]:
    """Register a background task on the selected hook registry.

    Background tasks start with the agent and keep running until shutdown. The
    task signature is inspected for state, context, and app-context dependencies
    so the runtime can inject matching values when the task is launched.

    Async background tasks run in the event loop. Synchronous ones are wrapped
    in ``WrappedThreadedBackgroundTask`` and executed through ``run_threaded``.
    State changes they make publish to the agent that adopted the state, as
    changes made outside a task.

    Args:
        *args: Background function to register when used as ``@background``
            without parentheses.
        name: Explicit registry key. Defaults to the function name.
        registry: Hook registry to populate. Required: without one this raises
            ``NoRegistryError``, since hooks go through an app (``@app.background``).

    Returns:
        The original function, or a decorator configured with the provided
        metadata.

    Raises:
        ValueError: If more than one function is passed at once.

    Reached through ``AppRegistry.background``; call it directly only with
    ``registry=``.

    Examples:
        Register a long-running async background loop::

            @app.background
            async def heartbeat(state: MyState) -> None:
                while True:
                    state.counter += 1
                    await asyncio.sleep(1)
    """

    if len(args) > 1:
        raise ValueError("You can only register one function at a time.")
    if len(args) == 1:
        function = args[0]
        if registry is None:
            raise NoRegistryError.for_decorator(
                "Hooks go", "background", "async def my_hook(): ...", "registry"
            )
        name = name or function.__name__
        if asyncio.iscoroutinefunction(function):
            a = cast(AsyncBackgroundFunction, function)
            registry.register_background(name, WrappedBackgroundTask(a, structure_registry))
        else:
            assert inspect.isfunction(function) or inspect.ismethod(function), (
                "Function must be a async function or a sync function"
            )
            t = cast(ThreadedBackgroundFunction, function)
            registry.register_background(name, WrappedThreadedBackgroundTask(t, structure_registry))

        return cast(TBackground, function)

    else:

        def decorator(function: TBackground) -> TBackground:
            return cast(TBackground, background(function, name=name, registry=registry, structure_registry=structure_registry))

        return decorator
