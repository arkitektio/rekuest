from typing import Dict
from pydantic import Field
from rekuest.api.schema import TemplateFragment
from rekuest.postmans.graphql import GraphQLPostman
from rekuest.rath import RekuestRath
from rekuest.structures.default import get_default_structure_registry
from rekuest.structures.registry import (
    StructureRegistry,
)

from rekuest.definition.registry import (
    DefinitionRegistry,
    get_current_definition_registry,
    get_default_definition_registry,
)
from rekuest.agents.base import BaseAgent
from rekuest.postmans.base import BasePostman
from koil import unkoil
from koil.composition import Composition
from koil.decorators import koilable
from rekuest.register import register
from rekuest.agents.extension import AgentExtension


@koilable(fieldname="koil", add_connectors=True)
class Rekuest(Composition):
    rath: RekuestRath = Field(default_factory=RekuestRath)
    structure_registry: StructureRegistry = Field(
        default_factory=get_default_structure_registry
    )
    agent: BaseAgent = Field(default_factory=BaseAgent)
    postman: BasePostman = Field(default_factory=GraphQLPostman)

    registered_templates: Dict[str, TemplateFragment] = Field(default_factory=dict)

    def register(self, *args, **kwargs) -> None:
        """
        Register a new function
        """
        structure_registry = kwargs.pop("structure_registry", self.structure_registry)
        definition_registry = kwargs.pop(
            "definition_registry", self.agent.definition_registry
        )

        return register(
            *args,
            definition_registry=definition_registry,
            structure_registry=structure_registry,
            **kwargs,
        )

    def register_extension(self, name: str, extension: AgentExtension) -> None:
        """Register an extension on the agent.

        Extensions are used to allow to hook into the template registration and
        actor registration process. This allows to add new templates and actors
        to the agent.
        """
        return self.agent.register_extension(name, extension=extension)

    def run(self, *args, **kwargs) -> None:
        """
        Run the application.
        """
        return unkoil(self.arun, *args, **kwargs)

    async def arun(self) -> None:
        """
        Run the application.
        """
        await self.agent.aprovide()

    def _repr_html_inline_(self):
        return f"<table><tr><td>rath</td><td>{self.rath._repr_html_inline_()}</td></tr></table>"

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True
        extra = "forbid"
