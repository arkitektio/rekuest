from typing import Any, Dict, List, Optional, Union
from rekuest.api.schema import (
    AssignationFragment,
    AssignationStatus,
    ReservationFragment,
    ReservationStatus,
)

from rekuest.messages import Assignation, Reservation
from rekuest.postmans.base import BasePostman
import asyncio
from pydantic import Field
import logging
from rekuest.postmans.transport.fakts import FaktsWebsocketPostmanTransport
from .transport.base import PostmanTransport
import uuid

logger = logging.getLogger(__name__)


class StatefulPostman(BasePostman):

    transport: Optional[PostmanTransport] = Field(
        default_factory=FaktsWebsocketPostmanTransport
    )

    assignations: Dict[str, Assignation] = Field(default_factory=dict)
    reservations: Dict[str, Reservation] = Field(default_factory=dict)

    _res_update_queues: Dict[str, asyncio.Queue] = {}
    _ass_update_queues: Dict[str, asyncio.Queue] = {}

    async def aconnect(self):
        await super().aconnect()

        data = await self.transport.alist_reservations()
        self.reservations = {res.reservation: res for res in data}

        data = await self.transport.alist_assignations()
        self.assignations = {ass.assignation: ass for ass in data}

    async def areserve(
        self,
        node: str,
        params: dict = None,
        provision: str = None,
        reference: str = None,
    ) -> asyncio.Queue[ReservationFragment]:
        reservation = await self.transport.areserve(
            node, params, provision=provision, reference=reference
        )
        self.reservations[reservation.reservation] = reservation
        return reservation

    async def aunreserve(self, reservation_id: str) -> ReservationFragment:
        unreservation = await self.transport.aunreserve(reservation_id)
        self.reservations[
            unreservation.reservation
        ].status = ReservationStatus.CANCELING
        return self.reservations[unreservation.reservation]

    async def aassign(
        self,
        reservation: str,
        args: List[Any],
        persist=True,
        log=False,
    ) -> asyncio.Queue[AssignationFragment]:
        assignation = await self.transport.aassign(reservation, args, persist, log)
        self.assignations[assignation.assignation] = assignation
        return assignation

    async def aunassign(
        self,
        assignation: str,
    ) -> AssignationFragment:
        print(assignation)
        unassignation = await self.transport.aunassign(assignation)
        self.assignations[
            unassignation.assignation
        ].status = AssignationStatus.CANCELING
        return unassignation

    def register_reservation_queue(
        self, node: str, reference: str, queue: asyncio.Queue
    ):
        self._res_update_queues[node + reference] = queue

    def register_assignation_queue(self, ass_id: str, queue: asyncio.Queue):
        self._ass_update_queues[ass_id] = queue

    async def abroadcast(self, message: Union[Assignation, Reservation]):
        if isinstance(message, Assignation):
            if message.assignation in self._ass_update_queues:
                self.assignations[message.assignation].update(message)
                await self._ass_update_queues[message.assignation].put(
                    self.assignations[message.assignation]
                )
            else:
                logger.warning(
                    "Received Assignation Update without having knowingly queued it. Most likely because client crashed before receiving updates.  We will omit!"
                )
        elif isinstance(message, Reservation):
            if message.reservation in self._res_update_queues:
                self.reservations[message.reservation].update(message)
                await self._res_update_queues[message.reservation].put(
                    self.reservations[message.reservation]
                )
            else:
                logger.warning(
                    "Received Reservation Update without having knowingly queued it. Most likely because client crashed before receiving updates. We will omit!"
                )

        else:
            raise Exception("Unknown message type")

    def unregister_reservation_queue(self, node: str, reference: str):
        del self._res_update_queues[node + reference]

    def unregister_assignation_queue(self, ass_id: str):
        del self._ass_update_queues[ass_id]

    async def __aenter__(self):
        await self.transport.__aenter__()
        return await super().__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.transport.__aexit__(exc_type, exc_val, exc_tb)
        return await super().__aexit__(exc_type, exc_val, exc_tb)
