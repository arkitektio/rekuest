from typing import Dict, Union

from pydantic import BaseModel, Field, PrivateAttr
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
from rekuest.actors.errors import UnknownMessageError, ProvisionDelegateException
from koil.types import Contextual
from rekuest.definition.define import DefinitionInput
from typing import Protocol, runtime_checkable, Optional, List, Any
from rekuest.actors.transport.types import ActorTransport, AssignTransport
from rekuest.collection.collector import ActorCollector, AssignationCollector
from rekuest.definition.registry import (
    DefinitionRegistry,
)
from rekuest.actors.types import Assignment, Passport, Unassignment
import uuid
from rekuest.collection.collector import Collector

logger = logging.getLogger(__name__)


class Actor(BaseModel):
    passport: Passport
    transport: ActorTransport
    collector: Collector
    definition_registry: DefinitionRegistry
    managed_actors: Dict[str, "Actor"] = Field(default_factory=dict)
    running_assignments: Dict[str, Assignment] = Field(default_factory=dict)

    _in_queue: Contextual[asyncio.Queue] = PrivateAttr(default=None)
    _running_asyncio_tasks: Dict[str, asyncio.Task] = PrivateAttr(default_factory=dict)
    _running_transports: Dict[str, AssignTransport] = PrivateAttr(default_factory=dict)
    _provision_task: asyncio.Task = PrivateAttr(default=None)
    _status: ProvisionStatus = PrivateAttr(default=ProvisionStatus.PENDING)

    async def on_provide(self, passport: Passport):
        return None

    async def on_unprovide(self):
        return None

    async def on_assign(
        self,
        assignment: Assignment,
        collector: AssignationCollector,
        transport: AssignTransport,
    ):
        raise NotImplementedError(
            "Needs to be owerwritten in Actor Subclass. Never use this class directly"
        )

    async def apass(self, message: Union[Unassignment, Assignment]):
        assert self._in_queue, "Actor is currently not listening"
        await self._in_queue.put(message)

    async def arun(self):
        self._in_queue = asyncio.Queue()
        self._provision_task = asyncio.create_task(self.alisten())
        return self._provision_task

    async def aset_status(self, status: ProvisionStatus, message: str = None):
        self._status = status
        await self.transport.change_provision(
            status=status,
            message=message or "No message provided",
        )

    async def aget_status(self):
        return self._status

    async def acancel(self):
        # Cancel Mnaged actors
        logger.info(f"Cancelling Actor {self.passport.id}")
        if self.managed_actors:
            logger.info("Cancelling Actors")
            for actor in self.managed_actors.values():
                logger.info(
                    f"Cancelling managed actor with passport {actor.passport.id}"
                )
                await actor.acancel()

        if not self._provision_task or self._provision_task.done():
            # Race condition
            return

        self._provision_task.cancel()

        try:
            await self._provision_task
        except asyncio.CancelledError:
            logger.info(f"Actor {self.passport.id} was cancelled")

    async def aass_log(self, id: str, message: str, level=AssignationLogLevel.INFO):
        logging.critical(f"ASS {id} {message}")
        await self.transport.log_to_assignation(id=id, level=level, message=message)
        logging.critical(f"ASS SEND {message}")

    async def aprov_log(self, message: str, level=ProvisionLogLevel.INFO):
        logging.critical(f"PROV {self.passport} {message}")
        await self.transport.log_to_provision(
            id=self.passport, level=level, message=message
        )

    async def provide(self):
        try:
            logging.info(f"Providing {self.passport}")
            await self.on_provide(self.passport)
            await self.aset_status(
                status=ProvisionStatus.ACTIVE,
            )

        except Exception as e:
            logging.critical(f"Providing Error {self.passport} {e}", exc_info=True)
            await self.aset_status(
                status=ProvisionStatus.CRITICAL,
                message=str(e),
            )

    async def unprovide(self):
        try:
            await self.on_unprovide()
            await self.aset_status(
                status=ProvisionStatus.INACTIVE,
            )

        except Exception as e:
            logging.critical(f"Unproviding Error {self.passport} {e}", exc_info=True)
            await self.aset_status(
                status=ProvisionStatus.CRITICAL,
                message=str(e),
            )

    async def aprocess(self, message: Union[Assignment, Unassignment]):
        logger.info(f"Actor for {self.passport}: Received {message}")

        if isinstance(message, Assignment):
            transport = self.transport.spawn(message)

            task = asyncio.create_task(
                self.on_assign(
                    message,
                    collector=self.collector,
                    transport=transport,
                )
            )

            self._running_transports[message.id] = transport
            self._running_asyncio_tasks[message.id] = task

        elif isinstance(message, Unassignment):
            if message.id in self._running_asyncio_tasks:
                task = self._running_asyncio_tasks[message.id]
                transport = self._running_transports[message.id]

                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        logger.info(
                            f"Task {transport.assignment} was cancelled through arkitekt. Setting Cancelled"
                        )
                        await transport.change_assignation(
                            status=AssignationStatus.CANCELLED,
                            message="Cancelled by Actor",
                        )

                        del self._running_asyncio_tasks[message.id]
                        del self._running_transports[message.id]

                else:
                    logger.warning(
                        f"Race Condition: Task was already done before cancellation"
                    )

            else:
                logger.warning(
                    f"Actor for {self.passport}: Received unassignment for unknown assignation {message.id}"
                )
        else:
            raise UnknownMessageError(f"{message}")

    async def alisten(self):
        try:
            await self.provide()
            logger.info(f"Actor for {self.passport}: Is now active")

            while True:
                message = await self._in_queue.get()
                try:
                    await self.aprocess(message)
                except Exception as e:
                    logger.critical(
                        "Processing unknown message should never happen", exc_info=True
                    )

        except asyncio.CancelledError:
            logger.info("Doing Whatever needs to be done to cancel!")

            [i.cancel() for i in self._running_asyncio_tasks.values()]

            for task, transport in zip(
                self._running_asyncio_tasks.values(), self._running_transports.values()
            ):
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info(
                        f"Task {transport.assignment} was cancelled through applicaction. Setting Critical"
                    )
                    await transport.change_assignation(
                        status=AssignationStatus.CRITICAL,
                        message="Cancelled by Application",
                    )

            await self.unprovide()

        except Exception as e:
            logger.critical("Unhandled exception", exc_info=True)

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

    async def aspawn_actor(
        self, interface: str, transport: ActorTransport
    ) -> "SerializingActor":
        """Spawns an Actor from the definition of the given interface"""
        try:
            actor_builder = self.definition_registry.get_builder_for_interface(
                interface
            )

        except KeyError as e:
            raise ProvisionDelegateException(
                "No Actor Builder found for interface"
            ) from e

        passport = Passport(provision=self.passport.provision, parent=self.passport.id)

        actor = actor_builder(
            passport=passport,
            transport=transport,
            definition_registry=self.definition_registry,
            collector=self.collector,
        )
        self.managed_actors[passport.id] = actor
        return actor

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


Actor.update_forward_refs()
SerializingActor.update_forward_refs()
