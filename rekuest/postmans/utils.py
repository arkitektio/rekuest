from random import random
from typing import (
    Awaitable,
    Callable,
    Optional,
    Union,
    TypeVar,
    runtime_checkable,
    Protocol,
    Any,
    Dict,
    List,
    Tuple,
    AsyncIterator,
)
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
from rekuest.structures.serialization.postman import (
    shrink_inputs,
    expand_outputs,
    serialize_inputs,
    deserialize_outputs,
)
from rekuest.structures.registry import StructureRegistry
from rekuest.api.schema import (
    DefinitionFragment,
    DefinitionInput,
    ReserveBindsInput,
    afind,
)
from rekuest.agents.base import BaseAgent
from rekuest.actors.base import Actor, SerializingActor
from rekuest.agents.transport.base import AgentTransport
from rekuest.actors.transport.local_transport import (
    LocalTransport,
    ProxyActorTransport,
    ProxyAssignTransport,
)
from rekuest.definition.validate import auto_validate
from .base import BasePostman
from rekuest.messages import Provision
import asyncio
from rekuest.agents.transport.protocols.agent_json import (
    AssignationChangedMessage,
    ProvisionChangedMessage,
    ProvisionMode,
)
from rekuest.actors.transport.types import ActorTransport, AssignTransport
from rekuest.definition.registry import DefinitionRegistry
from rekuest.actors.types import Passport, Assignment, Unassignment, AssignmentUpdate
from rekuest.structures.serialization.postman import (
    serialize_inputs,
    deserialize_outputs,
)
from enum import Enum

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ContractStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


@runtime_checkable
class ContractStateHook(Protocol):
    async def __call__(self, state: ContractStatus) -> None:
        ...


@runtime_checkable
class RPCContract(Protocol):
    async def __aenter__(self: Any) -> Any:
        ...

    async def __aexit__(self, exc_type, exc, tb):
        ...

    async def change_state(self, state: ContractStatus):
        ...

    async def aassign(
        self,
        args: List[Any],
        kwargs: Dict[str, Any],
        parent: Assignment,
        assign_timeout: float = 10,
    ) -> List[Any]:
        ...

    async def astream(
        self,
        args: List[Any],
        kwargs: Dict[str, Any],
        parent: Assignment,
        yield_timeout: float = 10,
    ) -> AsyncIterator[List[Any]]:
        ...


class RPCContractBase(KoiledModel):
    active: ContextBool = Field(default=False)
    state: ContractStatus = Field(default=ContractStatus.INACTIVE)
    state_hook: Optional[ContractStateHook] = None

    async def aenter(self):
        raise NotImplementedError("Should be implemented by subclass")

    async def aexit(self):
        raise NotImplementedError("Should be implemented by subclass")

    async def change_state(self, state: ContractStatus):
        self.state = state
        if self.state_hook:
            await self.state_hook(state)

    async def aassign(
        self,
        *args,
        **kwargs,
    ):
        raise NotImplementedError("Should be implemented by subclass")

    async def astream(
        self,
        *args,
        **kwargs,
    ):
        raise NotImplementedError("Should be implemented by subclass")

    async def __aenter__(self: T) -> T:
        await self.aenter()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aexit()


class actoruse(RPCContractBase):
    interface: str
    supervisor: Actor
    reference: Optional[str]
    "The governing actor"
    assign_timeout: Optional[float] = 2000
    yield_timeout: Optional[float] = 2000

    _transport: AgentTransport = None
    _actor: SerializingActor
    _enter_future: asyncio.Future = None
    _exit_future: asyncio.Future = None
    _updates_queue: asyncio.Queue[
        Union[AssignationChangedMessage, ProvisionChangedMessage]
    ] = None
    _updates_watcher: asyncio.Task = None
    _assign_queues = {}

    async def aassign(
        self,
        args: List[Any],
        kwargs: Dict[str, Any],
        parent: Assignment,
        assign_timeout: float = 10,
    ) -> List[Any]:
        assignment = Assignment(
            assignation=parent.assignation,
            parent=parent.id,
            args=serialize_inputs(self._actor.definition, args, kwargs),
            status=AssignationStatus.ASSIGNED,
            user=parent.user,
        )

        _ass_queue = asyncio.Queue[AssignmentUpdate]()
        self._assign_queues[assignment.id] = _ass_queue

        await self._actor.apass(assignment)
        try:
            while True:  # Waiting for assignation
                ass = await asyncio.wait_for(
                    _ass_queue.get(), timeout=assign_timeout or self.assign_timeout
                )
                if ass.status == AssignationStatus.RETURNED:
                    return deserialize_outputs(self._actor.definition, ass.returns)

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

    async def on_actor_log(self, *args, **kwargs):
        print(args, kwargs)

    async def on_assign_log(self, *args, **kwargs):
        print(args, kwargs)

    async def on_actor_change(self, status: ProvisionStatus, **kwargs):
        print(status)
        if status == ProvisionStatus.ACTIVE:
            await self.change_state(ContractStatus.ACTIVE)
            if self._enter_future and not self._enter_future.done():
                self._enter_future.set_result(True)

        if status == ProvisionStatus.CRITICAL:
            await self.change_state(ContractStatus.INACTIVE)
            if self._enter_future and not self._enter_future.done():
                self._enter_future.set_exception(Exception("Error on provision"))

    async def astream(
        self,
        args: List[Any],
        kwargs: Dict[str, Any],
        parent: Assignment,
        yield_timeout: float = 10,
    ) -> AsyncIterator[List[Any]]:
        inputs = serialize_inputs(self._actor.definition, args, kwargs)
        assignment = Assignment(
            assignation=parent.assignation,
            parent=parent.id,
            args=inputs,
            status=AssignationStatus.ASSIGNED,
        )

        _ass_queue = asyncio.Queue[AssignmentUpdate]()
        self._assign_queues[assignment.id] = _ass_queue

        await self._actor.apass(assignment)

        try:
            while True:  # Waiting for assignation
                ass = await asyncio.wait_for(
                    _ass_queue.get(), timeout=yield_timeout or self.yield_timeout
                )
                logger.info(f"Reservation Context: {ass}")
                if ass.status == AssignationStatus.YIELD:
                    yield deserialize_outputs(self._actor.definition, ass.returns)

                if ass.status == AssignationStatus.DONE:
                    return

                if ass.status in [AssignationStatus.CRITICAL, AssignationStatus.ERROR]:
                    raise Exception(f"Critical error: {ass.message}")

        except asyncio.CancelledError as e:
            await self._actor.apass(
                Unassignment(assignation=assignment.id, id=assignment.id)
            )

            ass = await asyncio.wait_for(_ass_queue.get(), timeout=2)
            if ass.status == AssignationStatus.CANCELING:
                logger.info("Wonderfully cancelled that assignation!")
                raise e

            raise e

    async def on_assign_change(
        self, assignment: Assignment, status=None, returns=None, progress=None
    ):
        await self._assign_queues[assignment.id].put(
            AssignmentUpdate(
                assignment=assignment.id,
                status=status,
                returns=returns,
                progress=progress,
            )
        )

        return

    async def aenter(self):
        self._enter_future = asyncio.Future()
        self._updates_queue = asyncio.Queue[AssignationChangedMessage]()

        self._actor = await self.supervisor.aspawn_actor(
            self.interface,
            ProxyActorTransport(
                on_log=self.on_actor_log,
                on_change=self.on_actor_change,
                on_assign_change=self.on_assign_change,
                on_assign_log=self.on_assign_log,
            ),
        )

        await self._actor.arun()
        await self._enter_future

    async def aexit(self):
        if self._actor:
            await self._actor.acancel()

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True
        copy_on_model_validation = False


class arkiuse(RPCContractBase):
    hash: Optional[str] = None
    provision: Optional[str] = None
    reference: str = "default"
    binds: Optional[ReserveBindsInput] = None
    params: Optional[ReserveParamsInput] = None
    postman: BasePostman
    reserve_timeout: Optional[float] = 2000
    assign_timeout: Optional[float] = 2000
    yield_timeout: Optional[float] = 2000

    _reservation: ReservationFragment = None
    _enter_future: asyncio.Future = None
    _exit_future: asyncio.Future = None
    _updates_queue: asyncio.Queue = None
    _updates_watcher: asyncio.Task = None
    _definition: Optional[DefinitionFragment] = None

    async def aassign(
        self,
        args: List[Any],
        kwargs: Dict[str, Any],
        parent: Assignment,
        assign_timeout: float = 10,
    ) -> List[Any]:
        assert self._reservation, "We never entered the context manager"
        assert (
            self._reservation.status == ReservationStatus.ACTIVE
        ), "Reservation is not active"

        inputs = serialize_inputs(self._definition, args, kwargs)

        _ass_queue = await self.postman.aassign(
            self._reservation.id,
            inputs,
            parent=parent.assignation,
        )

        ass = None
        try:
            while True:  # Waiting for assignation
                ass = await asyncio.wait_for(
                    _ass_queue.get(), timeout=assign_timeout or self.assign_timeout
                )
                logger.info(f"Reservation Context: {ass}")
                if ass.status == AssignationStatus.RETURNED:
                    return deserialize_outputs(self._definition, ass.returns)

                if ass.status in [AssignationStatus.CRITICAL, AssignationStatus.ERROR]:
                    raise Exception(f"Critical error: {ass.statusmessage}")
        except asyncio.CancelledError as e:
            if ass:
                await self.postman.aunassign(ass.id)

                ass = await asyncio.wait_for(_ass_queue.get(), timeout=2)
                if ass.status == AssignationStatus.CANCELING:
                    logger.info("Wonderfully cancelled that assignation!")
                    raise e

                raise Exception(f"Critical error: {ass}")

    async def astream(
        self,
        args: List[Any],
        kwargs: Dict[str, Any],
        parent: Assignment,
        yield_timeout: float = 10,
    ) -> AsyncIterator[List[Any]]:
        assert self._reservation, "We never entered the context manager"
        assert (
            self._reservation.status == ReservationStatus.ACTIVE
        ), "Reservation is not active"

        _ass_queue = await self.postman.aassign(
            self._reservation.id,
            serialize_inputs(self._definition, args, kwargs),
            parent=parent.assignation,
        )
        ass = None

        try:
            while True:  # Waiting for assignation
                ass = await asyncio.wait_for(
                    _ass_queue.get(), timeout=yield_timeout or self.yield_timeout
                )
                logger.info(f"Reservation Context: {ass}")
                if ass.status == AssignationStatus.YIELD:
                    yield deserialize_outputs(self._definition, ass.returns)

                if ass.status == AssignationStatus.DONE:
                    return

                if ass.status in [AssignationStatus.CRITICAL, AssignationStatus.ERROR]:
                    raise Exception(f"Critical error: {ass.statusmessage}")

        except asyncio.CancelledError as e:
            if ass:
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

                    await self.change_state(ContractStatus.ACTIVE)

                elif self._reservation.status == ReservationStatus.CRITICAL:
                    if self._enter_future and not self._enter_future.done():
                        self._enter_future.set_exception(True)

                    await self.change_state(ContractStatus.INACTIVE)

                else:
                    print("Crrently unhandled status")

        except asyncio.CancelledError:
            pass

    async def aenter(self):
        logger.info(f"Trying to reserve {self.hash}")

        self._enter_future = asyncio.Future()
        self._definition = await afind(hash=self.hash)
        self._updates_queue = await self.postman.areserve(
            hash=self.hash,
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
