from random import random
from typing import Awaitable, Callable, Optional, Union, TypeVar
import uuid

from pydantic import Field
from rekuest.messages import Assignation, Reservation, Unassignation
from rekuest.structures.registry import get_current_structure_registry
from koil.composition import KoiledModel
from koil.helpers import unkoil_gen
from koil.types import ContextBool
from rekuest.api.schema import (
    AssignationFragment,
    AssignationLogLevel,
    AssignationStatus,
    ProvisionStatus,
    ReservationFragment,
    ReservationStatus,
    ReserveParamsInput,
    NodeFragment,
)
import uuid
import asyncio
from koil import unkoil
import logging
from rekuest.structures.serialization.postman import shrink_inputs, expand_outputs
from rekuest.structures.registry import StructureRegistry
from rekuest.api.schema import DefinitionFragment, DefinitionInput, ReserveBindsInput
from rekuest.agents.base import BaseAgent
from rekuest.actors.base import Actor
from rekuest.agents.transport.base import AgentTransport
from rekuest.actors.transport.local_transport import LocalTransport
from rekuest.definition.validate import auto_validate
from .base import BasePostman
from rekuest.messages import Provision
import asyncio
from rekuest.agents.transport.protocols.agent_json import (
    AssignationChangedMessage,
    ProvisionChangedMessage,
    ProvisionMode,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RPCContract(KoiledModel):
    active: ContextBool = Field(default=False)
    shrink_inputs: bool = True
    expand_outputs: bool = True
    state: ReservationStatus = Field(default=ReservationStatus.DISCONNECTED)
    state_hook: Optional[Callable[[ReservationStatus], Awaitable[None]]] = None

    async def aenter(self):
        raise NotImplementedError("Should be implemented by subclass")

    async def aexit(self):
        raise NotImplementedError("Should be implemented by subclass")

    async def change_state(self, state: ReservationStatus):
        self.state = state
        if self.state_hook:
            await self.state_hook(state)

    async def aassign(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        **kwargs,
    ):
        raise NotImplementedError("Should be implemented by subclass")

    async def astream(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        **kwargs,
    ):
        raise NotImplementedError("Should be implemented by subclass")

    def assign(self, *args, **kwargs):
        return unkoil(self.aassign, *args, **kwargs)

    def stream(self, *args, **kwargs):
        return unkoil_gen(self.astream, *args, **kwargs)

    async def __aenter__(self: T) -> T:
        await self.aenter()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aexit()


class ReservationContract(RPCContract):
    # TODO:Assert that we can actually assign to this? validating that all of the nodes inputs are
    # registered in the structure registry?
    definition: NodeFragment
    provision: Optional[str] = None
    reference: str = "default"
    binds: Optional[ReserveBindsInput] = None
    params: Optional[ReserveParamsInput] = None
    auto_unreserve: bool = False
    shrink_inputs: bool = True
    expand_outputs: bool = True
    res_log: Optional[
        Callable[[Reservation, AssignationLogLevel, str], Awaitable[None]]
    ] = Field(default=None, exclude=True)
    on_reservation_change: Optional[
        Callable[[ReservationFragment], Awaitable[None]]
    ] = Field(default=None, exclude=True)

    active: ContextBool = False
    _reservation: Reservation = None

    async def aassign(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        **kwargs,
    ):
        raise NotImplementedError("Should be implemented by subclass")

    async def astream(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        **kwargs,
    ):
        raise NotImplementedError("Should be implemented by subclass")

    async def _alog(self, assignation, level, msg):
        if self.ass_log:  # pragma: no branch
            await self.ass_log(assignation, level, msg)

    async def _rlog(self, level, msg):
        if self.res_log:  # pragma: no branch
            await self.res_log(self._reservation, level, msg)

    def assign(self, *args, **kwargs):
        return unkoil(self.aassign, *args, **kwargs)

    def stream(self, *args, **kwargs):
        return unkoil_gen(self.astream, *args, **kwargs)

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            callable: lambda q: repr(q),
        }


class localuse(RPCContract):
    hash: str
    provision: str
    reference: str
    structure_registry: StructureRegistry
    agent: BaseAgent
    provide_timeout: Optional[float] = 2000
    assign_timeout: Optional[float] = 2000
    yield_timeout: Optional[float] = 2000

    _definition: DefinitionFragment = None
    _transport: AgentTransport = None
    _actor: Actor
    _enter_future: asyncio.Future = None
    _exit_future: asyncio.Future = None
    _updates_queue: asyncio.Queue[
        Union[AssignationChangedMessage, ProvisionChangedMessage]
    ] = None
    _updates_watcher: asyncio.Task = None
    _futures = {}
    _assign_queues = {}

    async def aassign(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        parent: Optional[Union[str, AssignationFragment]] = None,
        assign_timeout: Optional[float] = None,
        **kwargs,
    ):
        structure_registry = structure_registry or self.structure_registry
        inputs = await shrink_inputs(
            self._definition,
            args,
            kwargs,
            structure_registry=structure_registry,
            skip_shrinking=not self.shrink_inputs,
        )

        id = uuid.uuid4().hex  # TODO: Make this a proper uuid

        _ass_queue = asyncio.Queue[AssignationChangedMessage]()
        self._assign_queues[id] = _ass_queue

        await self._actor.apass(
            Assignation(
                assignation=id,
                parent=parent,
                args=inputs,
                status=AssignationStatus.ASSIGNED,
            )
        )
        try:
            while True:  # Waiting for assignation
                ass = await asyncio.wait_for(
                    _ass_queue.get(), timeout=assign_timeout or self.assign_timeout
                )
                if ass.status == AssignationStatus.RETURNED:
                    outputs = await expand_outputs(
                        self._definition,
                        ass.returns,
                        structure_registry=structure_registry,
                        skip_expanding=not self.expand_outputs,
                    )
                    return outputs

                if ass.status in [AssignationStatus.CRITICAL, AssignationStatus.ERROR]:
                    raise Exception(f"Critical error: {ass.message}")
        except asyncio.CancelledError as e:
            await self._actor.apass(
                Unassignation(
                    assignation=id,
                )
            )

            ass = await asyncio.wait_for(_ass_queue.get(), timeout=2)
            if ass.status == AssignationStatus.CANCELING:
                logger.info("Wonderfully cancelled that assignation!")
                raise e

            raise Exception(f"Critical error: {ass}")

    async def astream(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        parent: Optional[Union[str, AssignationFragment]] = None,
        yield_timeout: Optional[float] = None,
        **kwargs,
    ):
        structure_registry = structure_registry or self.structure_registry
        inputs = await shrink_inputs(
            self._definition,
            args,
            kwargs,
            structure_registry=structure_registry,
            skip_shrinking=not self.shrink_inputs,
        )

        id = uuid.uuid4().hex  # TODO: Make this a proper uuid

        _ass_queue = asyncio.Queue[AssignationChangedMessage]()
        self._assign_queues[id] = _ass_queue

        await self._actor.apass(
            Assignation(
                assignation=id,
                parent=parent,
                args=inputs,
                status=AssignationStatus.ASSIGNED,
            )
        )
        try:
            while True:  # Waiting for assignation
                ass = await asyncio.wait_for(
                    _ass_queue.get(), timeout=yield_timeout or self.yield_timeout
                )
                logger.info(f"Reservation Context: {ass}")
                if ass.status == AssignationStatus.YIELD:
                    outputs = await expand_outputs(
                        self._definition,
                        ass.returns,
                        structure_registry=structure_registry,
                        skip_expanding=not self.expand_outputs,
                    )
                    yield outputs

                if ass.status == AssignationStatus.DONE:
                    return

                if ass.status in [AssignationStatus.CRITICAL, AssignationStatus.ERROR]:
                    raise Exception(f"Critical error: {ass.message}")

        except asyncio.CancelledError as e:
            await self._actor.apass(
                Unassignation(
                    assignation=id,
                )
            )

            ass = await asyncio.wait_for(_ass_queue.get(), timeout=2)
            if ass.status == AssignationStatus.CANCELING:
                logger.info("Wonderfully cancelled that assignation!")
                raise e

            raise e

    async def watch_updates(self):
        logger.info("Waiting for updates")
        try:
            while True:
                message = await self._updates_queue.get()
                if isinstance(message, ProvisionChangedMessage):
                    if message.status == ProvisionStatus.ACTIVE:
                        if self._enter_future and not self._enter_future.done():
                            self._enter_future.set_result(True)

                if isinstance(message, AssignationChangedMessage):
                    assert (
                        message.assignation in self._assign_queues
                    ), "We never asked for this"
                    await self._assign_queues[message.assignation].put(message)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in updates watcher: {e}", exc_info=True)

    async def aenter(self):
        self._enter_future = asyncio.Future()
        self._updates_queue = asyncio.Queue[
            Union[AssignationChangedMessage, ProvisionChangedMessage]
        ]()

        template = self.agent.nodeHashTemplateMap[self.hash]
        self._definition = template.node

        actor_builder = self.agent._templateActorBuilderMap[template.id]

        provision = Provision(
            provision=self.provision, guardian=self.provision, template=template.id
        )

        self._transport = LocalTransport(broadcast=self._updates_queue.put)

        self._actor = actor_builder(provision=provision, transport=self._transport)
        await self._actor.arun()
        self._updates_watcher = asyncio.create_task(self.watch_updates())
        await self._enter_future

    async def aexit(self, *args, **kwargs):
        await self._actor.acancel()
        self._updates_watcher.cancel()

        try:
            await self._updates_watcher
        except asyncio.CancelledError:
            pass

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True
        copy_on_model_validation = False


class arkiuse(ReservationContract):
    postman: BasePostman
    structure_registry: StructureRegistry
    reserve_timeout: Optional[float] = 2000
    assign_timeout: Optional[float] = 2000
    yield_timeout: Optional[float] = 2000

    _reservation: ReservationFragment = None
    _enter_future: asyncio.Future = None
    _exit_future: asyncio.Future = None
    _updates_queue: asyncio.Queue = None
    _updates_watcher: asyncio.Task = None

    async def aassign(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        parent: Optional[Union[str, AssignationFragment]] = None,
        assign_timeout: Optional[float] = None,
        **kwargs,
    ):
        assert self._reservation, "We never entered the context manager"
        assert (
            self._reservation.status == ReservationStatus.ACTIVE
        ), "Reservation is not active"

        structure_registry = structure_registry or self.structure_registry
        inputs = await shrink_inputs(
            self.definition,
            args,
            kwargs,
            structure_registry=structure_registry,
            skip_shrinking=not self.shrink_inputs,
        )

        _ass_queue = await self.postman.aassign(
            self._reservation.id, inputs, parent=parent
        )
        try:
            while True:  # Waiting for assignation
                ass = await asyncio.wait_for(
                    _ass_queue.get(), timeout=assign_timeout or self.assign_timeout
                )
                logger.info(f"Reservation Context: {ass}")
                if ass.status == AssignationStatus.RETURNED:
                    outputs = await expand_outputs(
                        self.definition,
                        ass.returns,
                        structure_registry=structure_registry,
                        skip_expanding=not self.expand_outputs,
                    )
                    return outputs

                if ass.status in [AssignationStatus.CRITICAL, AssignationStatus.ERROR]:
                    raise Exception(f"Critical error: {ass.statusmessage}")
        except asyncio.CancelledError as e:
            await self.postman.aunassign(ass.id)

            ass = await asyncio.wait_for(_ass_queue.get(), timeout=2)
            if ass.status == AssignationStatus.CANCELING:
                logger.info("Wonderfully cancelled that assignation!")
                raise e

            raise Exception(f"Critical error: {ass}")

    async def astream(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        parent: Optional[Union[str, AssignationFragment]] = None,
        yield_timeout: Optional[float] = None,
        **kwargs,
    ):
        assert self._reservation, "We never entered the context manager"
        assert (
            self._reservation.status == ReservationStatus.ACTIVE
        ), "Reservation is not active"

        structure_registry = structure_registry or get_current_structure_registry()

        inputs = await shrink_inputs(
            self.definition,
            args,
            kwargs,
            structure_registry=structure_registry,
            skip_shrinking=not self.shrink_inputs,
        )

        _ass_queue = await self.postman.aassign(
            self._reservation.id,
            inputs,
            parent=parent,
        )
        try:
            while True:  # Waiting for assignation
                ass = await asyncio.wait_for(
                    _ass_queue.get(), timeout=yield_timeout or self.yield_timeout
                )
                logger.info(f"Reservation Context: {ass}")
                if ass.status == AssignationStatus.YIELD:
                    outputs = await expand_outputs(
                        self.definition,
                        ass.returns,
                        structure_registry=structure_registry,
                        skip_expanding=not self.expand_outputs,
                    )
                    yield outputs

                if ass.status == AssignationStatus.DONE:
                    return

                if ass.status in [AssignationStatus.CRITICAL, AssignationStatus.ERROR]:
                    raise Exception(f"Critical error: {ass.statusmessage}")

        except asyncio.CancelledError as e:
            logger.warning(f"Cancelling this assignation {ass}")
            await self.postman.aunassign(ass.id)

            ass = await asyncio.wait_for(_ass_queue.get(), timeout=2)
            if ass.status == AssignationStatus.CANCELING:
                logger.info("Wonderfully cancelled that assignation!")
                raise e

            raise e

    async def watch_updates(self):
        logger.info("Waiting for updates")
        try:
            while True:
                self._reservation = await self._updates_queue.get()
                logger.info(f"Updated Reservation {self._reservation}")
                if self._reservation.status == ReservationStatus.ACTIVE:
                    if self._enter_future and not self._enter_future.done():
                        logger.info("Entering future")
                        self._enter_future.set_result(True)

                await self.change_state(self._reservation.status)

        except asyncio.CancelledError:
            pass

    async def aenter(self):
        logger.info(f"Trying to reserve {self.definition}")

        self._enter_future = asyncio.Future()
        self._updates_queue = await self.postman.areserve(
            node=self.definition.id,
            params=self.params,
            provision=self.provision,
            reference=self.reference,
            binds=self.binds,
        )
        try:
            self._updates_watcher = asyncio.create_task(self.watch_updates())
            await asyncio.wait_for(
                self._enter_future, self.reserve_timeout
            )  # Waiting to enter

        except asyncio.TimeoutError:
            logger.warning("Reservation timeout")
            self._updates_watcher.cancel()

            try:
                await self._updates_watcher
            except asyncio.CancelledError:
                pass

            raise

        return self

    async def aexit(self):
        self.active = False

        if self.auto_unreserve:
            unreservation = await asyncio.wait_for(
                self.postman.aunreserve(self._reservation.id), timeout=1
            )
            logger.info(f"Unreserved {unreservation}")

        if self._updates_watcher:
            self._updates_watcher.cancel()

            try:
                await self._updates_watcher
            except asyncio.CancelledError:
                pass

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True
        copy_on_model_validation = False


class mockuse(RPCContract):
    returns: tuple = (1,)
    streamevents: int = 3
    assign_sleep: float = Field(default_factory=random)
    reserve_sleep: float = Field(default_factory=random)
    unreserve_sleep: float = Field(default_factory=random)
    stream_sleep: float = Field(default_factory=random)

    async def aenter(self):
        await asyncio.sleep(self.reserve_sleep)
        self.active = True
        return self

    async def aexit(self):
        self.active = False
        await asyncio.sleep(self.unreserve_sleep)

    async def aassign(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        **kwargs,
    ):
        assert self.active, "We never entered the contract"
        if alog:
            await alog(
                Assignation(assignation=str(uuid.uuid4())),
                AssignationLogLevel.INFO,
                "Mock assignation",
            )
        await asyncio.sleep(self.assign_sleep)
        return self.returns

    async def astream(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        **kwargs,
    ):
        assert self.active, "We never entered the contract"
        if alog:
            await alog(
                Assignation(assignation=str(uuid.uuid4())),
                AssignationLogLevel.INFO,
                "Mock assignation",
            )
        for i in range(self.streamevents):
            await asyncio.sleep(self.stream_sleep)
            yield self.returns

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True


class definitionmockuse(RPCContract):
    definition: Union[DefinitionFragment, DefinitionInput]
    should_assert: bool = True
    returns: tuple = (1,)
    streamevents: int = 3
    assign_sleep: float = Field(default_factory=random)
    reserve_sleep: float = Field(default_factory=random)
    unreserve_sleep: float = Field(default_factory=random)
    stream_sleep: float = Field(default_factory=random)

    _active = False

    async def aenter(self):
        if isinstance(self.definition, DefinitionInput):
            self.definition = auto_validate(self.definition)

        self._active = True
        await asyncio.sleep(self.reserve_sleep)
        return self

    async def aexit(self):
        await asyncio.sleep(self.unreserve_sleep)
        self._active = False

    async def aassign(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        **kwargs,
    ):
        assert self._active, "We never entered the context manager"
        if self.should_assert:
            assert len(args) == len(self.definition.args), "Wrong number of arguments"

        if alog:
            await alog(
                Assignation(assignation=str(uuid.uuid4())),
                AssignationLogLevel.INFO,
                "Mock assignation",
            )
        await asyncio.sleep(self.assign_sleep)

        return tuple(p.mock() for p in self.definition.returns)

    async def astream(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        **kwargs,
    ):
        assert self._active, "We never entered the context manager"
        if alog:
            await alog(
                Assignation(assignation=str(uuid.uuid4())),
                AssignationLogLevel.INFO,
                "Mock assignation",
            )
        for i in range(self.streamevents):
            await asyncio.sleep(self.stream_sleep)
            yield self.returns

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True
