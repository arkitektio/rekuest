from typing import Protocol, Awaitable, runtime_checkable
from rekuest.messages import Provision
from rekuest.agents.transport.base import AgentTransport
from .base import Actor
from rekuest.rath import RekuestRath
from rekuest.api.schema import TemplateFragment
from rekuest.definition.define import prepare_definition, DefinitionInput


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
