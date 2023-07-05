from rekuest.actors.types import Actifier
from rekuest.structures.registry import (
    StructureRegistry,
    get_current_structure_registry,
)
from rekuest.structures.default import (
    get_default_structure_registry,
)
from rekuest.definition.registry import (
    DefinitionRegistry,
    get_default_definition_registry,
    get_current_definition_registry,
)
from rekuest.api.schema import (
    WidgetInput,
    PortGroupInput,
    Scope,
    ReturnWidgetInput,
    EffectInput,
)
from rekuest.collection.shelve import get_current_shelve
from typing import Dict, List, Callable, Optional, Tuple, Awaitable, Any
import inflection
from rekuest.definition.define import prepare_definition
from rekuest.actors.actify import reactify
from functools import wraps, partial


def register_func(
    function_or_actor,
    structure_registry: StructureRegistry,
    definition_registry: DefinitionRegistry,
    interface: str = None,
    actifier: Actifier = reactify,
    port_groups: Optional[List[PortGroupInput]] = None,
    groups: Optional[Dict[str, List[str]]] = None,
    collections: List[str] = None,
    is_test_for: Optional[List[str]] = None,
    widgets: Dict[str, WidgetInput] = None,
    effects: Dict[str, List[EffectInput]] = None,
    interfaces: List[str] = [],
    on_provide=None,
    on_unprovide=None,
    **actifier_params,
):
    """Register a function or actor with the definition registry

    Register a function or actor with the definition registry. This will
    create a definition for the function or actor and register it with the
    definition registry.

    If first parameter is a function, it will be wrapped in an actorBuilder
    through the actifier. If the first parameter is an actor, it will be
    used as the actorBuilder (needs to have the dunder __definition__) to be
    detected as such.

    Args:
        function_or_actor (Union[Actor, Callable]): _description_
        actifier (Actifier, optional): _description_. Defaults to None.
        interface (str, optional): _description_. Defaults to None.
        widgets (Dict[str, WidgetInput], optional): _description_. Defaults to {}.
        interfaces (List[str], optional): _description_. Defaults to [].
        on_provide (_type_, optional): _description_. Defaults to None.
        on_unprovide (_type_, optional): _description_. Defaults to None.
        structure_registry (StructureRegistry, optional): _description_. Defaults to None.
    """

    interface = interface or inflection.underscore(
        function_or_actor.__name__
    )  # convert this to camelcase

    assert (
        interface not in definition_registry.definitions
    ), "Interface already defined. Please choose a different name"

    definition, actor_builder = actifier(
        function_or_actor,
        structure_registry,
        on_provide=on_provide,
        on_unprovide=on_unprovide,
        widgets=widgets,
        is_test_for=is_test_for,
        collections=collections,
        groups=groups,
        port_groups=port_groups,
        effects=effects,
        interfaces=interfaces,
        **actifier_params,
    )

    definition_registry.register_at_interface(
        interface, definition, structure_registry, actor_builder
    )


def register(
    *func,
    actifier: Actifier = reactify,
    interface: str = None,
    widgets: Dict[str, WidgetInput] = None,
    interfaces: List[str] = [],
    collections: List[str] = None,
    port_groups: Optional[List[PortGroupInput]] = None,
    groups: Optional[Dict[str, List[str]]] = None,
    effects: Dict[str, List[EffectInput]] = None,
    is_test_for: Optional[List[str]] = None,
    on_provide=None,
    on_unprovide=None,
    structure_registry: StructureRegistry = None,
    definition_registry: DefinitionRegistry = None,
    **actifier_params,
):
    """Register a function or actor to the default definition registry.

    You can use this decorator to register a function or actor to the default
    definition registry. There is also a function version of this decorator,
    which is more convenient to use.

    Example:
        >>> @register
        >>> def hello_world(string: str):

        >>> @register(interface="hello_world")
        >>> def hello_world(string: str):

    Args:
        function_or_actor (Union[Callable, Actor]): The function or Actor
        builder (ActorBuilder, optional): An actor builder (see ActorBuilder). Defaults to None.
        package (str, optional): The package you want to register this function in. Defaults to standard app package    .
        interface (str, optional): The name of the function. Defaults to the functions name.
        widgets (Dict[str, WidgetInput], optional): A dictionary of parameter key and a widget. Defaults to the default widgets as registered in the structure registry .
        interfaces (List[str], optional): Interfaces that this node adheres to. Defaults to [].
        on_provide (Callable[[ProvisionFragment], Awaitable[dict]], optional): Function that shall be called on provide (in the async eventloop). Defaults to None.
        on_unprovide (Callable[[], Awaitable[dict]], optional): Function that shall be called on unprovide (in the async eventloop). Defaults to None.
        structure_registry (StructureRegistry, optional): The structure registry to use for this Actor (used to shrink and expand inputs). Defaults to None.
    """
    definition_registry = definition_registry or get_default_definition_registry()
    structure_registry = structure_registry or get_default_structure_registry()

    if len(func) > 1:
        raise ValueError("You can only register one function or actor at a time.")
    if len(func) == 1:
        function_or_actor = func[0]

        @wraps(function_or_actor)
        def wrapped_function(*args, **kwargs):
            return function_or_actor(*args, **kwargs)

        register_func(
            function_or_actor,
            structure_registry=structure_registry,
            definition_registry=definition_registry,
            actifier=actifier,
            interface=interface,
            is_test_for=is_test_for,
            widgets=widgets,
            effects=effects,
            collections=collections,
            interfaces=interfaces,
            on_provide=on_provide,
            on_unprovide=on_unprovide,
            port_groups=port_groups,
            groups=groups,
            **actifier_params,
        )

        return wrapped_function

    else:

        def real_decorator(function_or_actor):
            # Simple bypass for now
            def wrapped_function(*args, **kwargs):
                return function_or_actor(*args, **kwargs)

            register_func(
                function_or_actor,
                structure_registry=structure_registry,
                definition_registry=definition_registry,
                actifier=actifier,
                interface=interface,
                is_test_for=is_test_for,
                widgets=widgets,
                effects=effects,
                collections=collections,
                interfaces=interfaces,
                on_provide=on_provide,
                on_unprovide=on_unprovide,
                port_groups=port_groups,
                groups=groups,
                **actifier_params,
            )

            return wrapped_function

        return real_decorator


def register_structure(
    *cls,
    identifier: str = None,
    scope: Scope = Scope.LOCAL,
    expand: Callable[
        [
            str,
        ],
        Awaitable[Any],
    ] = None,
    shrink: Callable[
        [
            any,
        ],
        Awaitable[str],
    ] = None,
    convert_default: Callable[[Any], str] = None,
    default_widget: WidgetInput = None,
    default_returnwidget: ReturnWidgetInput = None,
    **kwargs,
):
    """Register a structure to the default structure registry.

    Args:
        cls (Structure): The structure class
        name (str, optional): The name of the structure. Defaults to the class name.
    """
    if len(cls) > 1:
        raise ValueError("You can only register one function or actor at a time.")
    if len(cls) == 1:
        function_or_actor = cls[0]

        get_default_structure_registry().register_as_structure(
            function_or_actor,
            identifier=identifier,
            scope=scope,
            shrink=shrink,
            expand=expand,
            convert_default=convert_default,
            default_widget=default_widget,
            default_returnwidget=default_returnwidget,
            **kwargs,
        )

        return cls

    else:

        def real_decorator(cls):
            # Simple bypass for now

            get_default_structure_registry().register_as_structure(
                cls,
                identifier=identifier,
                scope=scope,
                shrink=shrink,
                expand=expand,
                convert_default=convert_default,
                default_widget=default_widget,
                default_returnwidget=default_returnwidget,
                **kwargs,
            )

            return cls

        return real_decorator


async def ashrink_to_shelve(data: Any):
    return await get_current_shelve().aput(data)


async def aexpand_to_shelve(data: Any):
    return await get_current_shelve().aput(data)


async def acollect_from_shelve(str: Any):
    return await get_current_shelve().adelete(str)


def register_shelved_structure(
    *cls,
    identifier: str = None,
    scope: Scope = Scope.LOCAL,
    expand: Callable[
        [
            str,
        ],
        Awaitable[Any],
    ] = None,
    shrink: Callable[
        [
            any,
        ],
        Awaitable[str],
    ] = None,
    convert_default: Callable[[Any], str] = None,
    default_widget: WidgetInput = None,
    default_returnwidget: ReturnWidgetInput = None,
    **kwargs,
):
    """Register a structure to the default structure registry.

    Args:
        cls (Structure): The structure class
        name (str, optional): The name of the structure. Defaults to the class name.
    """
    if len(cls) > 1:
        raise ValueError("You can only register one function or actor at a time.")
    if len(cls) == 1:
        function_or_actor = cls[0]

        get_default_structure_registry().register_as_structure(
            function_or_actor,
            identifier=identifier,
            scope=scope,
            shrink=shrink,
            expand=expand,
            convert_default=convert_default,
            default_widget=default_widget,
            default_returnwidget=default_returnwidget,
            **kwargs,
        )

        return cls

    else:

        def real_decorator(cls):
            # Simple bypass for now

            get_default_structure_registry().register_as_structure(
                cls,
                identifier=identifier,
                scope=scope,
                shrink=shrink,
                expand=expand,
                convert_default=convert_default,
                default_widget=default_widget,
                default_returnwidget=default_returnwidget,
                **kwargs,
            )

            return cls

        return real_decorator
