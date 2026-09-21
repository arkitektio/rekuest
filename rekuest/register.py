"""Register a function or actor with the definition registry."""

from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Literal,
    Optional,
    ParamSpec,
    TypeVar,
    overload,
    cast,
)
from collections.abc import Callable

if TYPE_CHECKING:
    from rekuest.app import AppRegistry
from rekuest.errors import NoRegistryError
from rekuest.coercible_types import (
    OptimisticCoercible,
)
from rekuest.actors.actify import reactify
from rekuest.actors.policy import KEEP, DisconnectPolicy
from rekuest.actors.types import Actifier, ActorBuilder, RegisterConfig
from rekuest.definition.define import (
    dependency_to_dependency_input,
)
from rekuest.definition.utils import interface_name
from rekuest.definition.dependencies import build_action_dependency_input
from rekuest.definition.hash import hash_definition
from rekuest.protocols import AnyFunction
from rekuest.structures.registry import StructureRegistry
from rekuest.api.schema import (
    AssignWidgetInput,
    DefinitionInput,
    ActionDependencyInput,
    TrackInput,
    PortGroupInput,
    EffectInput,
    ImplementationInput,
    ValidatorInput,
    AgentDependencyInput,
    OptimisticInput,
    TestTargetInput,
)
import functools
import logging


logger = logging.getLogger(__name__)


P = ParamSpec("P")
R = TypeVar("R")


class WrappedFunction(Generic[P, R]):
    """A registered function: still the plain function, plus what it registered as.

    Calling it remotely goes through a client, which knows the app it runs in:
    ``rekuest.call(fn, ...)`` finds its implementation there.
    """

    def __init__(
        self, func: Callable[P, R], interface: str, definition: DefinitionInput
    ) -> None:
        """Initialize the wrapped function."""
        functools.update_wrapper(self, func)  # name, doc, signature: still the function
        self.func = func
        self.interface = interface
        self.definition = definition
        self.hash = hash_definition(definition)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        """Call the actor's implementation."""
        return self.func(*args, **kwargs)

    def __reduce__(self) -> str:
        """Pickle by name, like the function it replaced at module level.

        The decorated name now holds this wrapper, so pickling the inner function
        by reference would find the wrapper there and refuse.

        Returns:
            The qualified name pickle looks the wrapper up under.
        """
        qualname: str = getattr(self.func, "__qualname__")
        return qualname

    def to_dependency_input(self) -> ActionDependencyInput:
        """Convert the wrapped function to a DependencyInput."""
        return build_action_dependency_input(
            key=self.interface,
            definition=self.definition,
            optional=False,
        )


def register_func(
    function_or_actor: AnyFunction,
    structure_registry: StructureRegistry,
    implementation_registry: "AppRegistry",
    config: RegisterConfig | None = None,
    *,
    actifier: Actifier = reactify,
) -> tuple[DefinitionInput, ActorBuilder]:
    """Register a function or actor with the provided app registry.

    This function wraps a callable or actor into an ActorBuilder and registers it
    with an AppRegistry instance, at ``config.interface`` or an interface name
    inferred from the function name.

    Args:
        function_or_actor (AnyFunction): A function or actor to be registered.
        structure_registry (StructureRegistry): The registry used for structuring inputs.
        implementation_registry (AppRegistry): The registry where implementations are stored.
        config (Optional[RegisterConfig], optional): Bundled registration options.
            Defaults to an empty ``RegisterConfig``.
        actifier (Actifier, optional): Callable converting functions to actors. Defaults to reactify.

    Returns:
        Tuple[DefinitionInput, ActorBuilder]: Registered definition and its actor builder.
    """
    config = config or RegisterConfig()
    interface = config.interface or interface_name(function_or_actor)

    definition, implementation_details, actor_builder = actifier(
        function_or_actor,
        structure_registry,
        config,
    )

    dependencies: list[AgentDependencyInput] = []
    for (
        key,
        dependency,
    ) in implementation_details.dependency_variables.dependency_variables.items():
        dependencies.append(
            dependency_to_dependency_input(key, dependency, structure_registry)
        )

    optimistics: list[OptimisticInput] = [
        optimistic
        if isinstance(optimistic, OptimisticInput)
        else optimistic.to_optimistic_input()
        for optimistic in (config.optimistics or [])
    ]

    implementation_registry.register_at_interface(
        interface,
        ImplementationInput(
            interface=interface,
            definition=definition,
            locks=tuple(implementation_details.locks or []),
            optimistics=tuple(optimistics),
            dependencies=tuple(dependencies),
            tracks=tuple(implementation_details.tracks or []),
            needs_token=True,  # TODO: Make this configurable in the future, but for now, we want to ensure that all actors require tokens for security reasons.
            manipulates=tuple(implementation_details.manipulates or []),
        ),
        actor_builder,
    )

    return definition, actor_builder


T = TypeVar("T", bound=AnyFunction)


@overload
def register(func: Callable[P, R]) -> WrappedFunction[P, R]:
    """Register a function or actor directly: ``@register``."""
    ...


@overload
def register(
    *,
    name: str | None = None,
    description: str | None = None,
    actifier: Actifier = reactify,
    interface: str | None = None,
    stateful: bool = False,
    widgets: dict[str, AssignWidgetInput] | None = None,
    collections: list[str] | None = None,
    port_groups: list[PortGroupInput] | None = None,
    effects: dict[str, list[EffectInput]] | None = None,
    is_test_for: list[TestTargetInput] | None = None,
    validators: dict[str, list[ValidatorInput]] | None = None,
    structure_registry: StructureRegistry | None = None,
    implementation_registry: Optional["AppRegistry"] = None,
    optimistics: list[OptimisticCoercible] | None = None,
    in_process: bool = False,
    tracks: list[TrackInput] | None = None,
    locks: list[str] | None = None,
    concurrency: Literal["parallel", "serial"] = "serial",
    policy: DisconnectPolicy = KEEP,
    version: str | None = None,
    catalogs: list[str] | None = None,
) -> Callable[[Callable[P, R]], WrappedFunction[P, R]]:
    """Register a function or actor with configuration: ``@register(...)``."""
    ...


def register(  # type: ignore[valid-type]
    *func: Callable[P, R],
    name: str | None = None,
    actifier: Actifier = reactify,
    interface: str | None = None,
    stateful: bool = False,
    description: str | None = None,
    widgets: dict[str, AssignWidgetInput] | None = None,
    collections: list[str] | None = None,
    port_groups: list[PortGroupInput] | None = None,
    effects: dict[str, list[EffectInput]] | None = None,
    is_test_for: list[TestTargetInput] | None = None,
    optimistics: list[OptimisticCoercible] | None = None,
    validators: dict[str, list[ValidatorInput]] | None = None,
    structure_registry: StructureRegistry | None = None,
    tracks: list[TrackInput] | None = None,
    implementation_registry: Optional["AppRegistry"] = None,
    in_process: bool = False,
    locks: list[str] | None = None,
    concurrency: Literal["parallel", "serial"] = "serial",
    policy: DisconnectPolicy = KEEP,
    version: str | None = None,
    catalogs: list[str] | None = None,
) -> WrappedFunction[P, R] | Callable[[Callable[P, R]], WrappedFunction[P, R]]:
    """Register a function or actor with an app registry.

    Serves as both a bare decorator and a configurable decorator. All keyword
    arguments are bundled into a single :class:`RegisterConfig` that is threaded
    through ``register_func`` and the actifier.

    Use this as:
        @register
        def my_function(...): ...

    Or with arguments:
        @register(interface="custom_interface", widgets={...})
        def my_function(...): ...

    Args:
        *func: Function to register when used as a bare decorator.
        name (Optional[str]): Display name. Defaults to the function name.
        description (Optional[str]): Description. Defaults to the docstring.
        actifier (Actifier): Converts the callable into an actor builder.
            Defaults to :func:`reactify`.
        interface (Optional[str]): Interface name. Inferred from the function
            name if not provided.
        stateful (bool): Mark the definition stateful (auto-set when the
            function uses state variables).
        widgets (Optional[Dict[str, AssignWidgetInput]]): Widgets per argument.
        collections (Optional[List[str]]): Organizational groupings.
        port_groups (Optional[List[PortGroupInput]]): Port group assignments.
        effects (Optional[Dict[str, List[EffectInput]]]): Effects per port.
        is_test_for (Optional[List[TestTargetInput]]): Actions this function is a
            test for, each identified by hash or by (app, key, version).
        validators (Optional[Dict[str, List[ValidatorInput]]]): Input validation
            rules per argument.
        structure_registry (Optional[StructureRegistry]): Overrides the default
            structure registry.
        implementation_registry (Optional[AppRegistry]): Overrides the default
            app registry.
        optimistics (Optional[List[OptimisticCoercible]]): Optimistic outputs.
        in_process (bool): Run the actor in the event loop instead of a thread.
        tracks (Optional[List[TrackInput]]): Tracks the implementation follows.
        locks (Optional[List[str]]): Resource locks held during assignment
            (auto-inferred from state/context locks when omitted).
        concurrency (Literal["parallel", "serial"]): Whether assignments to the
            actor may run concurrently ("parallel") or one at a time
            ("serial", the default).
        version (Optional[str]): Version of the definition.

    Returns:
        The wrapped function, or a decorator producing it.
    """
    if implementation_registry is None:
        raise NoRegistryError.for_decorator(
            "Registration goes",
            "action",
            "def my_function(...): ...",
            "implementation_registry",
        )
    if structure_registry is None:
        structure_registry = implementation_registry.structure_registry

    config = RegisterConfig(
        name=name,
        description=description,
        interface=interface,
        widgets=widgets,
        effects=effects,
        validators=validators,
        collections=collections,
        port_groups=port_groups,
        is_test_for=is_test_for,
        stateful=stateful,
        version=version,
        catalogs=catalogs,
        optimistics=optimistics,
        locks=locks,
        concurrency=concurrency,
        policy=policy,
        tracks=tracks,
        in_process=in_process,
    )

    def _register(function_or_actor: Callable[P, R]) -> WrappedFunction[P, R]:
        any_function = cast(AnyFunction, function_or_actor)
        iface = config.interface or interface_name(any_function)

        definition, _ = register_func(
            any_function,
            structure_registry,
            implementation_registry,
            config,
            actifier=actifier,
        )

        return WrappedFunction(function_or_actor, iface, definition)

    if len(func) > 1:
        raise ValueError("You can only register one function or actor at a time.")
    if len(func) == 1:
        return _register(func[0])

    return cast(Callable[[T], T], _register)  # type: ignore
