"""Register a class as a state of an app."""

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Optional,
    TypeVar,
    overload,
    get_type_hints,
)
from collections.abc import Callable
from rekuest.errors import NoRegistryError
from rekuest.api.schema import (
    ReturnPortInput,
    StateImplementationInput,
    StateDefinitionInput,
)
from rekuest.structures.registry import StructureRegistry
from fieldz import fields, Field

if TYPE_CHECKING:
    from rekuest.app import AppRegistry

T = TypeVar("T")


def inspect_state(
    cls: type[T], name: str, structure_registry: StructureRegistry
) -> StateImplementationInput:
    """The schema of a state class, its ports built against ``structure_registry``."""
    from rekuest.definition.define import convert_object_to_returnport

    ports: list[ReturnPortInput] = []

    try:
        resolved_hints = get_type_hints(cls, include_extras=True)
    except Exception:
        resolved_hints = {}

    for field in fields(cls):  # type: ignore
        type_ = resolved_hints.get(field.name) or field.type or field.annotated_type
        if type_ is None:
            raise ValueError(
                f"Field {field.name} has no type annotation. Please add a type annotation."
            )

        port = convert_object_to_returnport(
            cls=type_,
            key=field.name,
            description=field.description or field.metadata.get("description", None),
            validators=field.metadata.get("validators", None),
            label=field.metadata.get("label", None),
            default=field.default if field.default != Field.MISSING else None,
            registry=structure_registry,
        )
        ports.append(port)

    return StateImplementationInput(
        interface=name,
        definition=StateDefinitionInput(ports=tuple(ports), name=name),
    )


@overload
def state(*function: type[T]) -> type[T]: ...


@overload
def state(
    *,
    name: str | None = None,
    required_locks: list[str] | None = None,
    publish_interval: float = 0.1,
    registry: Optional["AppRegistry"] = None,
    structure_reg: StructureRegistry | None = None,
) -> Callable[[T], T]: ...


def state(
    *function: type[T],
    name: str | None = None,
    required_locks: list[str] | None = None,
    publish_interval: float = 0.1,
    registry: Optional["AppRegistry"] = None,
    structure_reg: StructureRegistry | None = None,
) -> type[T] | Callable[[type[T]], type[T]]:
    """Register a class as an observable state of an app.

    The class is made a dataclass if it is not one, its schema is inspected
    against the app's structures, and the app registry records it under the
    given name with its rules. The class itself is returned unchanged: it is
    the *app* that knows it as a state, so one class can be a state of any
    number of apps. An instance becomes evented when an agent adopts it (see
    :func:`~rekuest.state.observable.evented`).

    Args:
        *function: Class to decorate when used as ``@state`` without
            parentheses.
        name: Explicit exported state name. Defaults to the class name.
        required_locks: Locks that must be held while mutating this state.
        publish_interval: Debounce interval for published state updates.
        registry: App registry to populate. Required: without one this raises
            ``NoRegistryError``, since state goes through an app (``@app.state``).
        structure_reg: Structure registry used while inspecting the state
            schema. Defaults to the app registry's.

    Returns:
        The decorated class, or a decorator configured with the provided
        metadata.

    Raises:
        ValueError: If more than one class is passed at once.

    Reached through ``AppRegistry.state``; call it directly only with
    ``registry=``.

    Examples:
        Register a state class that is observable by the runtime::

            @app.state(name="camera_state", required_locks=["camera"])
            class CameraState:
                connected: bool = False
                exposure_ms: float = 10.0
    """
    if registry is None:
        raise NoRegistryError.for_decorator(
            "State goes", "state", "class MyState: ...", "registry"
        )
    structure_registry = structure_reg or registry.structure_registry

    if len(function) == 1:
        cls = function[0]
        return state(
            name=name or cls.__name__,
            required_locks=required_locks,
            publish_interval=publish_interval,
            registry=registry,
            structure_reg=structure_registry,
        )(cls)

    if len(function) == 0:

        def wrapper(cls: type[T]) -> type[T]:
            # Ensure it's a dataclass
            try:
                fields(cls)
            except TypeError:
                cls = dataclass(cls)

            interface = cls.__name__ if name is None else name
            registry.register_state(
                cls,
                inspect_state(cls, interface, structure_registry),
                structure_registry,
                required_locks=required_locks,
                publish_interval=publish_interval,
            )
            return cls

        return wrapper

    raise ValueError("You can only register one class at a time.")
