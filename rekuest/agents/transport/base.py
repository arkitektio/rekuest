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


class AgentTransport(KoiledModel):
    """Agent Transport

    A Transport is a means of communicating with an Agent. It is responsible for sending
    and receiving messages from the backend. It needs to implement the following methods:


    change_assignation: Changing the status of an assignation. (depends on the backend)
    change_provision: Changing the status of an provision. (depends on the backend)
    log_to_assignation: Logging to an assignation. (depends on the backend)
    log_to_provision: Logging to an provision. (depends on the backend)

    aget_message: Getting a message from the backend. (depends on the backend)


    if it is a stateful connection it can also implement the following methods:

    aconnect
    adisconnect

    """

    @property
    def connected(self):
        return NotImplementedError("Implement this method")

    @abstractmethod
    async def change_provision(
        self,
        id: str,
        status: ProvisionStatus = None,
        message: str = None,
        mode: ProvisionMode = None,
    ):
        raise NotImplementedError("This is an abstract Base Class")

    @abstractmethod
    async def change_assignation(
        self,
        id: str,
        status: AssignationStatus = None,
        message: str = None,
        returns: List[Any] = None,
        progress: int = None,
    ):
        raise NotImplementedError("This is an abstract Base Class")

    @abstractmethod
    async def log_to_provision(
        self,
        id: str,
        level: LogLevelInput = None,
        message: str = None,
    ):
        raise NotImplementedError("This is an abstract Base Class")

    @abstractmethod
    async def log_to_assignation(
        self,
        id: str,
        level: LogLevelInput = None,
        message: str = None,
    ):
        raise NotImplementedError("This is an abstract Base Class")

    @abstractmethod
    async def aget_message(
        self,
    ) -> Union[Assignation, Provision, Unassignation, Unprovision]:
        """This method should return a message from the backend. It should await until a message is received.
        That then will be processed by the agent.
        """
        raise NotImplementedError("This is an abstract Base Class")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    class Config:
        underscore_attrs_are_private = True
        arbitrary_types_allowed = True
        copy_on_model_validation = False
