from typing import Callable, Dict, List, Optional, Tuple, Union

from pydantic import Field
import inspect
from rekuest.actors.base import Actor
from rekuest.actors.builder import ActorBuilder
from rekuest.agents.errors import ProvisionException
from rekuest.api.schema import (
    ProvisionFragment,
    TemplateFragment,
    acreate_template,
    afind,
    aget_provision,
)
from rekuest.definition.registry import (
    DefinitionRegistry,
    get_current_definition_registry,
)
from rekuest.rath import RekuestRath, current_rekuest_rath
import asyncio
from rekuest.agents.transport.base import AgentTransport, Contextual
from rekuest.messages import Assignation, Unassignation, Unprovision, Provision
from koil import unkoil
from koil.composition import KoiledModel
import logging


logger = logging.getLogger(__name__)


class BaseAgent(KoiledModel):
    """Agent

    Agents are the governing entities for every app. They are responsible for
    managing the lifecycle of the direct actors that are spawned from them through arkitekt.

    Agents are nothing else than actors in the classic distributed actor model, but they are
    always provided when the app starts and they do not provide functionality themselves but rather
    manage the lifecycle of the actors that are spawned from them.

    The actors that are spawned from them are called guardian actors and they are the ones that+
    provide the functionality of the app. These actors can then in turn spawn other actors that
    are not guardian actors. These actors are called non-guardian actors and their lifecycle is
    managed by the guardian actors that spawned them. This allows for a hierarchical structure
    of actors that can be spawned from the agents.


    """

    instance_id: str = "main"
    transport: AgentTransport
    definition_registry: Optional[DefinitionRegistry] = None

    provisionActorMap: Dict[str, Actor] = Field(default_factory=dict)

    rath: Optional[RekuestRath] = None

    _hooks = {}

    _approved_templates: List[Tuple[TemplateFragment, Callable]] = []
    _templateActorBuilderMap: Dict[str, ActorBuilder] = {}
    _localActorBuilderMap: Dict[str, ActorBuilder] = {}
    _templateTemplatesMap: Dict[str, TemplateFragment] = {}
    _provisionTaskMap: Dict[str, asyncio.Task] = Field(default_factory=dict)
    _inqueue: Contextual[asyncio.Queue] = None

    started = False
    running = False

    async def abroadcast(
        self, message: Union[Assignation, Provision, Unassignation, Unprovision]
    ):
        await self._inqueue.put(message)

    async def process(self):
        raise NotImplementedError(
            "This method needs to be implemented by the agents subclass"
        )

    async def aregister_definitions(self):

        if self.definition_registry.defined_nodes:
            for (
                definition,
                actor_builder,
                params,
            ) in self.definition_registry.defined_nodes:
                # Defined Node are nodes that are not yet reflected on arkitekt (i.e they dont have an instance
                # id so we are trying to send them to arkitekt)
                arkitekt_template = await acreate_template(
                    definition=definition,
                    params={},  # Todo really make this happen
                    instance_id=self.instance_id,
                    rath=self.rath,
                )

                self._approved_templates.append(
                    (arkitekt_template, actor_builder, params)
                )

                self._localActorBuilderMap[arkitekt_template.node.hash] = actor_builder
                self._templateActorBuilderMap[arkitekt_template.id] = actor_builder
                self._templateTemplatesMap[arkitekt_template.id] = arkitekt_template

    async def aspawn_actor(self, prov: Provision) -> Actor:
        """Spawns an Actor from a Provision"""
        try:
            actor_builder = self._templateActorBuilderMap[prov.template]
        except KeyError as e:
            raise ProvisionException("No Actor Builder found for template") from e
        actor = actor_builder(provision=prov, transport=self.transport)
        task = await actor.arun()
        task.add_done_callback(print)
        self.provisionActorMap[prov.provision] = actor
        return actor

    async def astep(self):
        await self.process(await self._inqueue.get())

    async def astart(self):
        await self.aregister_definitions()

        await self.transport.aconnect(self.instance_id)

        data = await self.transport.list_provisions()

        for prov in data:
            await self.abroadcast(prov)

        data = await self.transport.list_assignations()

        for ass in data:
            await self.abroadcast(ass)

        self.started = True

    def step(self, *args, **kwargs):
        return unkoil(self.astep, *args, **kwargs)

    def start(self, *args, **kwargs):
        return unkoil(self.astart, *args, **kwargs)

    def provide(self, *args, **kwargs):
        return unkoil(self.aprovide, *args, **kwargs)

    async def aloop(self):
        try:
            while True:
                self.running = True
                await self.astep()
        except asyncio.CancelledError:
            logger.info(
                f"Provisioning task cancelled. We are running {self.transport.instance_id}"
            )
            self.running = False
            raise

    async def aprovide(self):
        logger.info(
            f"Launching provisioning task. We are running {self.transport.instance_id}"
        )
        await self.astart()
        await self.aloop()

    async def __aenter__(self):
        self.definition_registry = (
            self.definition_registry or get_current_definition_registry()
        )
        self._inqueue = asyncio.Queue()
        self.transport._abroadcast = self.abroadcast
        await self.transport.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.transport.__aexit__(exc_type, exc_val, exc_tb)

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True
