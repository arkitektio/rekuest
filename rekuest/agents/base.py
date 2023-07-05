from typing import Callable, Dict, List, Optional, Tuple, Union

from pydantic import Field
from rekuest.actors.base import Actor
from rekuest.actors.types import ActorBuilder, Passport
from rekuest.agents.errors import ProvisionException
from rekuest.api.schema import (
    TemplateFragment,
    acreate_template,
    AssignationStatus,
    ProvisionStatus,
)
from rekuest.definition.registry import (
    DefinitionRegistry,
    get_current_definition_registry,
)
from rekuest.definition.registry import get_default_definition_registry
from rekuest.rath import RekuestRath
from rekuest.definition.validate import auto_validate
import asyncio
from rekuest.agents.transport.base import AgentTransport, Contextual
from rekuest.messages import Assignation, Unassignation, Unprovision, Provision, Inquiry
from koil import unkoil
from koil.composition import KoiledModel
import logging
from rekuest.collection.collector import Collector
import uuid
from rekuest.agents.errors import AgentException
from rekuest.actors.transport.local_transport import AgentActorTransport
from rekuest.actors.types import Assignment, Unassignment
from .transport.errors import DefiniteConnectionFail, CorrectableConnectionFail


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
    definition_registry: DefinitionRegistry = Field(
        default_factory=get_default_definition_registry
    )
    collector: Collector = Field(default_factory=Collector)
    managed_actors: Dict[str, Actor] = Field(default_factory=dict)

    rath: Optional[RekuestRath] = None

    _hooks = {}
    interface_template_map: Dict[str, TemplateFragment] = Field(default_factory=dict)
    template_interface_map: Dict[str, str] = Field(default_factory=dict)
    provision_passport_map: Dict[str, Passport] = Field(default_factory=dict)
    managed_assignments: Dict[str, Assignment] = Field(default_factory=dict)
    _provisionTaskMap: Dict[str, asyncio.Task] = Field(default_factory=dict)
    _inqueue: Contextual[asyncio.Queue] = None
    _errorfuture: Contextual[asyncio.Future] = None

    started = False
    running = False

    async def abroadcast(
        self, message: Union[Assignation, Provision, Unassignation, Unprovision]
    ):
        await self._inqueue.put(message)

    async def on_agent_error(self, exception) -> None:
        if self._errorfuture is None or self._errorfuture.done():
            return
        self._errorfuture.set_exception(exception)
        ...

    async def on_definite_error(self, error: DefiniteConnectionFail) -> None:
        if self._errorfuture is None or self._errorfuture.done():
            return
        self._errorfuture.set_exception(error)
        ...

    async def on_correctable_error(self, error: CorrectableConnectionFail) -> bool:
        # Always correctable
        return True
        ...

    async def process(
        self, message: Union[Assignation, Provision, Unassignation, Unprovision]
    ):
        logger.info(f"Agent received {message}")

        if isinstance(message, Assignation):
            if message.provision in self.provision_passport_map:
                passport = self.provision_passport_map[message.provision]
                actor = self.managed_actors[passport.id]

                # Converting assignation to Assignment
                message = Assignment(
                    assignation=message.assignation,
                    args=message.args,
                    user=message.user,
                )
                self.managed_assignments[message.assignation] = message
                await actor.apass(message)
            else:
                logger.warning(
                    "Received assignation for a provision that is not running"
                    f"Managed: {self.provision_passport_map} Received: {message.provision}"
                )
                await self.transport.change_assignation(
                    message.assignation,
                    status=AssignationStatus.CRITICAL,
                    message="Actor was no longer running or not managed",
                )

        elif isinstance(message, Inquiry):
            logger.info("Received Inquiry")
            for assignation in message.assignations:
                if assignation.assignation in self.managed_assignments:
                    logger.debug(
                        f"Received Inquiry for {assignation.assignation} and it was found. Ommiting setting Criticial"
                    )
                else:
                    logger.warning(
                        f"Did no find Inquiry for {assignation.assignation} and it was found. Setting Criticial"
                    )
                    await self.transport.change_assignation(
                        message.assignation,
                        status=AssignationStatus.CRITICAL,
                        message="Actor was no longer running or not managed",
                    )

        elif isinstance(message, Unassignation):
            if message.assignation in self.managed_assignments:
                passport = self.provision_passport_map[message.provision]
                actor = self.managed_actors[passport.id]
                assignment = self.managed_assignments[message.assignation]

                # Converting unassignation to unassignment
                unass = Unassignment(assignation=message.assignation, id=assignment.id)

                await actor.apass(unass)
            else:
                logger.warning(
                    "Received unassignation for a provision that is not running"
                    f"Managed: {self.provision_passport_map} Received: {message.provision}"
                )
                await self.transport.change_assignation(
                    message.assignation,
                    status=AssignationStatus.CRITICAL,
                    message="Actor was no longer running or not managed",
                )

        elif isinstance(message, Provision):
            # TODO: Check if the provision is already running
            try:
                status = await self.acheck_status_for_provision(message)
                await self.transport.change_provision(
                    message.provision,
                    status=status,
                    message="Actor was already running",
                )
            except KeyError as e:
                await self.aspawn_actor_from_provision(message)
            except Exception as e:
                logger.error("Spawning error", exc_info=True)
                await self.transport.change_provision(
                    message.provision, status=ProvisionStatus.DENIED, message=str(e)
                )

        elif isinstance(message, Unprovision):
            if message.provision in self.provision_passport_map:
                passport = self.provision_passport_map[message.provision]
                actor = self.managed_actors[passport.id]
                await actor.acancel()
                await self.transport.change_provision(
                    message.provision,
                    status=ProvisionStatus.CANCELLED,
                    message=str("Actor was cancelled"),
                )
                del self.provision_passport_map[message.provision]
                del self.managed_actors[passport.id]
                logger.info("Actor stopped")

            else:
                await self.transport.change_provision(
                    message.provision,
                    status=ProvisionStatus.CANCELLED,
                    message=str(
                        "Actor was no longer active when we received this message"
                    ),
                )
                logger.error(
                    f"Received Unprovision for never provisioned provision {message}"
                )

        else:
            raise AgentException(f"Unknown message type {type(message)}")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        cancelations = [actor.acancel() for actor in self.managed_actors.values()]
        # just stopping the actor, not cancelling the provision..

        for c in cancelations:
            try:
                await c
            except asyncio.CancelledError:
                pass

        await self.transport.__aexit__(exc_type, exc_val, exc_tb)

    async def aregister_definitions(self):
        """Registers the definitions that are defined in the definition registry

        This method is called by the agent when it starts and it is responsible for
        registering the definitions that are defined in the definition registry. This
        is done by sending the definitions to arkitekt and then storing the templates
        that are returned by arkitekt in the agent's internal data structures.

        You can implement this method in your agent subclass if you want define preregistration
        logic (like registering definitions in the definition registry).
        """

        for (
            interface,
            definition,
        ) in self.definition_registry.definitions.items():
            # Defined Node are nodes that are not yet reflected on arkitekt (i.e they dont have an instance
            # id so we are trying to send them to arkitekt)
            try:
                arkitekt_template = await acreate_template(
                    definition=definition,
                    interface=interface,
                    instance_id=self.instance_id,
                    rath=self.rath,
                )
            except Exception as e:
                logger.info(
                    f"Error Creating template for {definition} at interface {interface}"
                )
                raise e

            self.interface_template_map[interface] = arkitekt_template
            self.template_interface_map[arkitekt_template.id] = interface

    async def acheck_status_for_provision(
        self, provision: Provision
    ) -> ProvisionStatus:
        passport = self.provision_passport_map[provision.provision]
        actor = self.managed_actors[passport.id]
        return await actor.aget_status()

    async def aspawn_actor_from_provision(self, provision: Provision) -> Actor:
        """Spawns an Actor from a Provision. This function closely mimics the
        spawining protocol within an actor. But maps template"""

        try:
            interface = self.template_interface_map[provision.template]
        except KeyError as e:
            raise ProvisionException("No Interface found for requested template") from e

        try:
            actor_builder = self.definition_registry.get_builder_for_interface(
                interface
            )

        except KeyError as e:
            raise ProvisionException("No Actor Builder found for template") from e

        passport = Passport(provision=provision.provision)

        actor = actor_builder(
            passport=passport,
            transport=AgentActorTransport(
                passport=passport, agent_transport=self.transport
            ),
            definition_registry=self.definition_registry,
            collector=self.collector,
        )

        await actor.arun()  # TODO: Maybe move this outside?
        self.managed_actors[passport.id] = actor
        self.provision_passport_map[provision.provision] = passport
        return actor

    async def astep(self):
        queue_future = self._inqueue.get()
        done, pending = await asyncio.wait(
            [queue_future, self._errorfuture],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if self._errorfuture.done():
            print("Error future done")
            raise self._errorfuture.exception()
        else:
            await self.process(await done.pop())

    async def astart(self):
        await self.aregister_definitions()
        self._errorfuture = asyncio.Future()
        await self.transport.aconnect(self.instance_id)
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
                "Provisioning task cancelled. We are running"
                f" {self.transport.instance_id}"
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
        self.transport.set_callback(self)
        await self.transport.__aenter__()
        return self

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True
