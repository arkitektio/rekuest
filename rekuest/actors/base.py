from typing import Dict, Union

from pydantic import BaseModel, Field, PrivateAttr
from rekuest.agents.transport.base import AgentTransport
from rekuest.structures.registry import (
    StructureRegistry,
)
import asyncio
import logging
from rekuest.api.schema import (
    AssignationLogLevel,
    AssignationStatus,
    ProvisionLogLevel,
    ProvisionStatus,
    ProvisionMode,
    LogLevelInput,
)
from rekuest.messages import Assignation, Provision, Unassignation
from rekuest.actors.errors import UnknownMessageError
from koil.types import Contextual
from rekuest.definition.define import DefinitionInput
from typing import Protocol, runtime_checkable, Optional, List, Any
from rekuest.actors.transport.types import ActorTransport

logger = logging.getLogger(__name__)


class Actor(BaseModel):

    """A definition is a descriptor of the serialization of inputs and outputs of an actor. It is a necessary element to be registered on a rekuest server"""

    provision: Provision
    """ A provision is a providing request illustrating the context of the actor. This can be provided by a governing actor or by arkitekt itself. """

    transport: ActorTransport

    runningAssignments: Dict[str, Assignation] = Field(default_factory=dict)

    _in_queue: Contextual[asyncio.Queue] = PrivateAttr(default=None)
    _runningTasks: Dict[str, asyncio.Task] = PrivateAttr(default_factory=dict)
    _provision_task: asyncio.Task = PrivateAttr(default=None)

    async def on_provide(self, provision: Provision):
        return None

    async def on_unprovide(self):
        return None

    async def on_assign(self, assignation: Assignation):
        raise NotImplementedError(
            "Needs to be owerwritten in Actor Subclass. Never use this class directly"
        )

    async def apass(self, message: Union[Assignation, Unassignation]):
        assert self._in_queue, "Actor is currently not listening"
        await self._in_queue.put(message)

    async def arun(self):
        self._in_queue = asyncio.Queue()
        self._provision_task = asyncio.create_task(self.alisten())
        return self._provision_task

    async def acancel(self):
        """Cancel the actor and all its tasks, with its internal backoff strategy"""
        logger.info("We are getting cancelled here?")
        await self.astop()

    async def astop(self):
        if not self._provision_task or self._provision_task.done():
            return

        self._provision_task.cancel()

        try:
            await self._provision_task
        except asyncio.CancelledError:
            logger.info("Provision was cancelled")

    async def aass_log(self, id: str, message: str, level=AssignationLogLevel.INFO):
        logging.critical(f"ASS {id} {message}")
        await self.transport.log_to_assignation(id=id, level=level, message=message)
        logging.critical(f"ASS SEND {message}")

    async def aprov_log(self, message: str, level=ProvisionLogLevel.INFO):
        logging.critical(f"PROV {self.provision.provision} {message}")
        await self.transport.log_to_provision(
            id=self.provision.provision, level=level, message=message
        )

    async def provide(self):
        try:
            logging.info(f"Providing {self.provision.provision}")
            await self.on_provide(self.provision)
            await self.transport.change_provision(
                self.provision.provision,
                status=ProvisionStatus.ACTIVE,
            )

        except Exception as e:
            logging.critical(
                f"Providing Error {self.provision.provision} {e}", exc_info=True
            )
            await self.transport.change_provision(
                self.provision.provision,
                status=ProvisionStatus.CRITICAL,
                message=str(e),
            )

    async def unprovide(self):
        try:
            await self.on_unprovide()
            await self.transport.change_provision(
                self.provision.provision,
                status=ProvisionStatus.INACTIVE,
            )

        except Exception as e:
            logging.critical(
                f"Unproviding Error {self.provision.provision} {e}", exc_info=True
            )
            await self.transport.change_provision(
                self.provision.provision,
                status=ProvisionStatus.CRITICAL,
                message=str(e),
            )

    async def process(self, message: Union[Assignation, Unassignation]):
        logger.info(f"Actor for {self.provision}: Received {message}")

        if isinstance(message, Assignation):
            task = asyncio.create_task(self.on_assign(message))
            self.runningAssignments[message.assignation] = task
            return task

        elif isinstance(message, Unassignation):
            if message.assignation in self.runningAssignments:
                task = self.runningAssignments[message.assignation]
                if not task.done():
                    task.cancel()
                else:
                    logger.error("Task was already done")
            else:
                await self.transport.change_assignation(
                    message.assignation,
                    status=AssignationStatus.CRITICAL,
                    message="Task was never assigned",
                )
        else:
            raise UnknownMessageError(f"{message}")

    async def alisten(self):
        try:
            await self.provide()
            logger.info(f"Actor for {self.provision}: Is now active")

            while True:
                message = await self._in_queue.get()
                await self.process(message)

        except asyncio.CancelledError:
            logger.info("Doing Whatever needs to be done to cancel!")

            [i.cancel() for i in self.runningAssignments.values()]

            for i in self.runningAssignments.values():
                try:
                    await i
                except asyncio.CancelledError:
                    pass

            await self.unprovide()

            # TODO: Maybe send back an acknoledgement that we are done cancelling.
            # If we don't do this, arkitekt will not know if we failed to cancel our
            # tasks or if we succeeded. If we fail to cancel arkitekt can try to
            # kill the whole agent (maybe causing a sys.exit(1) or something)

        self._in_queue = None

    def _provision_task_done(self, task):
        logger.info(f"Provision task is done: {task}")
        if task.exception():
            raise task.exception()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._provision_task and not self._provision_task.done():
            await self.acancel()

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True
        copy_on_model_validation = "none"


class SerializingActor(Actor):
    definition: DefinitionInput
    structure_registry: StructureRegistry
    expand_inputs: bool = True
    shrink_outputs: bool = True
