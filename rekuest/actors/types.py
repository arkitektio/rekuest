from typing import Protocol, runtime_checkable, Callable, Awaitable, Any
from rekuest.structures.registry import StructureRegistry
from rekuest.messages import Provision
from rekuest.agents.transport.base import AgentTransport
from .base import Actor
from rekuest.rath import RekuestRath
from rekuest.api.schema import TemplateFragment, PortGroupInput
from rekuest.definition.define import DefinitionInput
from typing import Optional, List, Dict


@runtime_checkable
class ActorBuilder(Protocol):
    __definition__: DefinitionInput

    def __call__(
        self,
        provision: Provision,
        transport: AgentTransport,
        rath: RekuestRath,
        template: TemplateFragment,
    ) -> Actor:
        ...


@runtime_checkable
class Actifier(Protocol):
    """An actifier is a function that takes a callable and a structure registry
    as well as optional arguments

    """

    def __call__(
        self,
        function: Callable,
        structure_registry: StructureRegistry,
        port_groups: Optional[List[PortGroupInput]] = None,
        groups: Optional[Dict[str, List[str]]] = None,
        **kwargs
    ) -> ActorBuilder:
        ...


@runtime_checkable
class OnProvide(Protocol):
    """An on_provide is a function that takes a provision and a transport and returns
    an awaitable

    """

    def __call__(
        self, provision: Provision, transport: AgentTransport
    ) -> Awaitable[Any]:
        ...


@runtime_checkable
class OnUnprovide(Protocol):
    """An on_provide is a function that takes a provision and a transport and returns
    an awaitable

    """

    def __call__(self) -> Awaitable[Any]:
        ...
