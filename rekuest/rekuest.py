from textwrap import wrap
from typing import Any, Awaitable, Callable, Dict, List
from pydantic import Field
from rekuest.agents.stateful import StatefulAgent
from rekuest.api.schema import TemplateFragment, WidgetInput
from rekuest.postmans.graphql import GraphQLPostman
from rekuest.postmans.stateful import StatefulPostman
from rekuest.rath import RekuestRath
from rekuest.messages import Provision
from rekuest.structures.default import get_default_structure_registry
from rekuest.structures.registry import (
    StructureRegistry,
)

from rekuest.definition.registry import (
    DefinitionRegistry,
    get_current_definition_registry,
)
from rekuest.agents.base import BaseAgent
from rekuest.postmans.base import BasePostman
from koil import unkoil
from koil.composition import Composition
from koil.decorators import koilable
from rekuest.api.schema import acreate_template
from rekuest.actors.builder import ActorBuilder


@koilable(fieldname="koil", add_connectors=True)
class Rekuest(Composition):
    rath: RekuestRath = Field(default_factory=RekuestRath)
    structure_registry: StructureRegistry = Field(
        default_factory=get_default_structure_registry
    )
    definition_registry: DefinitionRegistry = Field(
        default_factory=get_current_definition_registry
    )
    agent: BaseAgent = Field(default_factory=StatefulAgent)
    postman: BasePostman = Field(default_factory=GraphQLPostman)

    registered_templates: Dict[str, TemplateFragment] = Field(default_factory=dict)

    def register(self, *args, **kwargs) -> None:
        """
        Register a new function
        """
        structure_registry = kwargs.get("structure_registry", self.structure_registry)

        def real_decorator(function_or_actor):

            self.definition_registry.register(
                function_or_actor,
                *args,
                structure_registry=structure_registry,
                **kwargs
            )

            return function_or_actor

        return real_decorator

    def run(self, *args, **kwargs) -> None:
        """
        Run the application.
        """
        return unkoil(self.arun, *args, **kwargs)

    async def arun(self, instance_id: str = "default") -> None:
        """
        Run the application.
        """
        assert self.agent.transport.connected, "Transport is not connected"
        await self.agent.aprovide(instance_id=instance_id)

    def _repr_html_inline_(self):
        return f"<table><tr><td>rath</td><td>{self.rath._repr_html_inline_()}</td></tr></table>"


    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True
        extra = "forbid"
