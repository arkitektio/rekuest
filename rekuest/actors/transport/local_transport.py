from abc import abstractmethod
from typing import Any, Awaitable, Callable, List, Optional, Union

from rekuest.messages import Assignation, Unassignation, Provision, Unprovision
from rekuest.api.schema import (
    LogLevelInput,
    ProvisionMode,
    ProvisionStatus,
    AssignationStatus,
)
from koil.composition import KoiledModel
from typing import Protocol, runtime_checkable
from rekuest.agents.transport.protocols.agent_json import (
    AssignationChangedMessage,
    ProvisionChangedMessage,
    ProvisionMode,
)
import logging
import asyncio

logger = logging.getLogger(__name__)


@runtime_checkable
class Broadcast(Protocol):
    def __call__(
        self,
        assignation: Union[Assignation, Unassignation, Provision, Unprovision],
    ) -> Awaitable[None]:
        ...


class LocalTransport(KoiledModel):
    """Agent Transport

    A Transport is a means of communicating with an Agent. It is responsible for sending
    and receiving messages from the backend. It needs to implement the following methods:

    list_provision: Getting the list of active provisions from the backend. (depends on the backend)
    list_assignation: Getting the list of active assignations from the backend. (depends on the backend)

    change_assignation: Changing the status of an assignation. (depends on the backend)
    change_provision: Changing the status of an provision. (depends on the backend)

    broadcast: Configuring the callbacks for the transport on new assignation, unassignation provision and unprovison.

    if it is a stateful connection it can also implement the following methods:

    aconnect
    adisconnect

    """

    broadcast: Broadcast

    @property
    def connected(self):
        print("Called connected")
        return True

    async def change_provision(
        self,
        id: str,
        status: ProvisionStatus = None,
        message: str = None,
        mode: ProvisionMode = None,
    ):
        print("change_provision", id, status, message, mode)  #
        await self.broadcast(
            ProvisionChangedMessage(
                provision=id, status=status, message=message, mode=mode
            )
        )

    async def change_assignation(
        self,
        id: str,
        status: AssignationStatus = None,
        message: str = None,
        returns: List[Any] = None,
        progress: int = None,
    ):
        print("change_assignation", id, status, message, returns, progress)
        await self.broadcast(
            AssignationChangedMessage(
                assignation=id, status=status, message=message, returns=returns
            )
        )

    async def log_to_provision(
        self,
        id: str,
        level: LogLevelInput = None,
        message: str = None,
    ):
        print("log_to_provision", id, level, message)
        logger.info(f"{id} {level} {message}")

    async def log_to_assignation(
        self,
        id: str,
        level: LogLevelInput = None,
        message: str = None,
    ):
        print("log_to_assignation", id, level, message)
        logger.info(f"{id} {level} {message}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    class Config:
        underscore_attrs_are_private = True
        arbitrary_types_allowed = True
        copy_on_model_validation = False
