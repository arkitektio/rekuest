import contextvars
from ..actors.base import Actor
from rekuest.api.schema import DefinitionInput
from rekuest.actors.actify import reactify, Actifier
from rekuest.definition.define import prepare_definition
from rekuest.structures.registry import (
    StructureRegistry,
    get_current_structure_registry,
)
from rekuest.api.schema import WidgetInput
from typing import Dict, List, Callable, Optional, Tuple
from pydantic import Field
from koil.composition import KoiledModel
from typing import Protocol, runtime_checkable
import json
from rekuest.actors.builder import ActorBuilder


current_definition_registry = contextvars.ContextVar(
    "current_definition_registry", default=None
)
GLOBAL_DEFINITION_REGISTRY = None


def get_default_definition_registry():
    global GLOBAL_DEFINITION_REGISTRY
    if GLOBAL_DEFINITION_REGISTRY is None:
        GLOBAL_DEFINITION_REGISTRY = DefinitionRegistry()
    return GLOBAL_DEFINITION_REGISTRY


def get_current_definition_registry(allow_global=True):
    return current_definition_registry.get(get_default_definition_registry())


class DefinitionRegistry(KoiledModel):
    structure_registry: Optional[StructureRegistry] = None
    defined_nodes: List[Tuple[DefinitionInput, Callable]] = Field(
        default_factory=list, exclude=True
    )
    definitions: Dict[DefinitionInput, ActorBuilder] = Field(
        default_factory=dict, exclude=True
    )
    actifier: Actifier = reactify
    copy_from_default: bool = False

    _token: contextvars.Token = None

    def has_definitions(self):
        return len(self.defined_nodes) > 0 or len(self.templated_nodes) > 0

    def reset(self):
        self.defined_nodes = []  # dict are queryparams for the node
        self.templated_nodes = []

    def register_actorBuilder(self, actorBuilder: ActorBuilder, **params):  # New Node
        self.defined_nodes.append((actorBuilder.__definition__, actorBuilder, params))
        self.definitions[actorBuilder.__definition__] = actorBuilder

    def register(
        self,
        function_or_actor,
        structure_registry: StructureRegistry,
        actifier: Actifier = None,
        interface: str = None,
        widgets: Dict[str, WidgetInput] = None,
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

        if hasattr(function_or_actor, "__definition__"):
            actorBuilder = function_or_actor

        else:
            actifier = actifier or self.actifier
            actorBuilder = actifier(
                function_or_actor,
                structure_registry,
                on_provide=on_provide,
                on_unprovide=on_unprovide,
                widgets=widgets,
                interfaces=interfaces,
                **actifier_params,
            )

        assert hasattr(
            actorBuilder, "__definition__"
        ), "The actorBuilder needs to have a definition. Otherwise it is not a valid actorBuilder"

        self.register_actorBuilder(actorBuilder)

    async def __aenter__(self):
        return self

    def dump(self):
        return {
            "definitions": [
                json.loads(x[0].json(exclude_none=True, exclude_unset=True))
                for x in self.defined_nodes
            ],
            "templates": [x[0] for x in self.templated_nodes],
        }

    async def __aexit__(self, *args, **kwargs):
        current_definition_registry.set(None)


def register(
    widgets: Dict[str, WidgetInput] = {},
    interfaces: List[str] = [],
    on_provide=None,
    on_unprovide=None,
    definition_registry: DefinitionRegistry = None,
    structure_registry: StructureRegistry = None,
    **params,
):
    """Take a function and register it as a node.

    This function is used to register a node. Use it as a decorator. You can specify
    specific widgets for every paramer in a dictionary {argument_key: widget}. By default
    this function will use the default defintion registry to store the nodes inputdata.
    This definition registry will then be used by an agent to create, and provide the node.

    If your function has specific inputs that need custom rules for expansion and shrinking
     , you can pass a structure registry to the function. This registry will then be used.

    This decorator is non intrusive. You can still call this function as a normal function from
    your code

    Args:
        widgets (Dict[str, WidgetInput], optional): _description_. Defaults to {}.
        interfaces (List[str], optional): _description_. Defaults to [].
        on_provide (_type_, optional): _description_. Defaults to None.
        on_unprovide (_type_, optional): _description_. Defaults to None.
        definition_registry (DefinitionRegistry, optional): _description_. Defaults to None.
        structure_registry (StructureRegistry, optional): _description_. Defaults to None.

    Returns:
        Callable: A wrapped function that just returns the original function.
    """
    definition_registry = definition_registry or get_current_definition_registry()
    structure_registry = structure_registry or get_current_structure_registry()

    def real_decorator(function):
        # Simple bypass for now
        def wrapped_function(*args, **kwargs):
            return function(*args, **kwargs)

        definition_registry.register(
            function,
            widgets=widgets,
            interfaces=interfaces,
            structure_registry=structure_registry,
            on_provide=on_provide,
            on_unprovide=on_unprovide,
            **params,
        )

    return real_decorator
