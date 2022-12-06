from rekuest.actors.base import Actor
from rekuest.actors.functional import (
    FunctionalFuncActor,
    FunctionalGenActor,
    FunctionalThreadedFuncActor,
    FunctionalThreadedGenActor,
)

import inspect
from rekuest.structures.registry import StructureRegistry
from rekuest.agents.transport.base import AgentTransport
from rekuest.messages import Provision
import inspect
from .builder import ActorBuilder
from rekuest.definition.define import prepare_definition
from typing import Protocol, runtime_checkable, Callable


async def async_none_provide(prov: Provision):
    """Do nothing on provide"""
    return None


async def async_none_unprovide():
    """Do nothing on unprovide"""
    return None


def higher_order_builder(builder, **params):
    """Higher order builder for actors#

    This is a higher order builder for actors. It takes a Actor class and
    returns a builder function that inserts the parameters into the class
    constructor. Akin to a partial function.
    """

    def inside_builder(
        provision: Provision,
        transport: AgentTransport,
    ):

        return builder(
            provision=provision,
            transport=transport,
            **params,
        )

    inside_builder.__name__ = builder.__name__
    inside_builder.__definition__ = params["definition"]

    return inside_builder


@runtime_checkable
class Actifier(Protocol):
    """An actifier is a function that takes a callable and a structure registry
    as well as optional arguments

    """

    def __call__(
        self, function: Callable, structure_registry: StructureRegistry, **kwargs
    ) -> ActorBuilder:
        ...


def reactify(
    function,
    structure_registry: StructureRegistry,
    bypass_shrink=False,
    bypass_expand=False,
    on_provide=None,
    widgets=None,
    interfaces=None,
    on_unprovide=None,
    **params,
) -> ActorBuilder:
    """Reactify a function

    This function takes a callable (of type async or sync function or generator) and returns a builder function that
    creates an actor that makes the function callable from the rekuest server. The callable will be both in the context
    of  an assignation and a provision helper, enabling the usage of the function as a provision helper.
    """

    definition = prepare_definition(
        function, structure_registry, widgets=widgets, interfaces=interfaces
    )

    is_coroutine = inspect.iscoroutinefunction(function)
    is_asyncgen = inspect.isasyncgenfunction(function)
    is_method = inspect.ismethod(function)

    is_generatorfunction = inspect.isgeneratorfunction(function)
    is_function = inspect.isfunction(function)

    actor_attributes = {
        "assign": function,
        "expand_inputs": not bypass_expand,
        "shrink_outputs": not bypass_shrink,
        "on_provide": on_provide if on_provide else async_none_provide,
        "on_unprovide": on_unprovide if on_unprovide else async_none_unprovide,
        "structure_registry": structure_registry,
        "definition": definition,
        **params,
    }

    if is_coroutine:
        return higher_order_builder(FunctionalFuncActor, **actor_attributes)
    elif is_asyncgen:
        return higher_order_builder(FunctionalGenActor, **actor_attributes)
    elif is_generatorfunction:
        return higher_order_builder(FunctionalThreadedGenActor, **actor_attributes)
    elif is_function or is_method:
        return higher_order_builder(FunctionalThreadedFuncActor, **actor_attributes)
    else:
        raise NotImplementedError("No way of converting this to a function")
