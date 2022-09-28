from ast import Assign
from random import Random, random
from typing import Awaitable, Callable, Optional, Union
import uuid

from pydantic import Field
from rekuest.messages import Assignation, Reservation
from rekuest.postmans.vars import current_postman
from rekuest.structures.registry import get_current_structure_registry
from koil.composition import KoiledModel
from koil.helpers import unkoil_gen
from koil.types import ContextBool
from .stateful import StatefulPostman
from rekuest.api.schema import (
    AssignationFragment,
    AssignationLogLevel,
    AssignationStatus,
    ReservationFragment,
    ReservationStatus,
    ReserveParamsInput,
)
import asyncio
from rekuest.traits.node import Reserve
from koil import unkoil
import logging
from rekuest.structures.serialization.postman import shrink_inputs, expand_outputs


logger = logging.getLogger(__name__)


class ReservationContract(KoiledModel):
    # TODO:Assert that we can actually assign to this? validating that all of the nodes inputs are
    # registered in the structure registry?
    node: Reserve
    provision: Optional[str] = None
    reference: str = "default"
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


class use(ReservationContract):
    postman: Optional[StatefulPostman] = None
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
        **kwargs,
    ):
        assert self._reservation, "We never entered the context manager"
        assert (
            self._reservation.status == ReservationStatus.ACTIVE
        ), "Reservation is not active"

        structure_registry = structure_registry or get_current_structure_registry()

        inputs = await shrink_inputs(
            self.node,
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
                ass = await _ass_queue.get()
                logger.info(f"Reservation Context: {ass}")
                if ass.status == AssignationStatus.RETURNED:
                    outputs = await expand_outputs(
                        self.node,
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
        **kwargs,
    ):
        assert self._reservation, "We never entered the context manager"
        assert (
            self._reservation.status == ReservationStatus.ACTIVE
        ), "Reservation is not active"

        structure_registry = structure_registry or get_current_structure_registry()

        inputs = await shrink_inputs(
            self.node,
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
                ass = await _ass_queue.get()
                logger.info(f"Reservation Context: {ass}")
                if ass.status == AssignationStatus.YIELD:
                    outputs = await expand_outputs(
                        self.node,
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

                if self.on_reservation_change:
                    await self.on_reservation_change(self._reservation)

        except asyncio.CancelledError:
            pass

    async def __aenter__(self):
        self.postman = self.postman or current_postman.get()
        logger.info(f"Trying to reserve {self.node}")

        self._enter_future = asyncio.Future()

        self._updates_queue = await self.postman.areserve(
            node=self.node.id,
            params=self.params,
            provision=self.provision,
            reference=self.reference,
        )

        self._updates_watcher = asyncio.create_task(self.watch_updates())
        await self._enter_future  # Waiting to enter
        return self

    async def __aexit__(self, *args, **kwargs):
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


class mockuse(ReservationContract):
    random_sleep: float = Field(default_factory=random)

    async def __aenter__(self):
        self.active = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.active = False

    async def aassign(
        self,
        *args,
        structure_registry=None,
        alog: Callable[[Assignation, AssignationLogLevel, str], Awaitable[None]] = None,
        **kwargs,
    ):
        assert self.active, "We never entered the context manager"
        if alog:
            await alog(
                Assignation(assignation=str(uuid.uuid4())),
                AssignationLogLevel.INFO,
                "Mock assignation",
            )
        await asyncio.sleep(self.random_sleep)
        return args
