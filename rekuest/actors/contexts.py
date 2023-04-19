from dataclasses import dataclass
from rekuest.messages import Assignation, Provision
from rekuest.actors.helper import AssignationHelper, ProvisionHelper
from rekuest.actors.vars import current_assignation_helper, current_provision_helper
from rekuest.actors.transport.types import ActorTransport
from pydantic import BaseModel


class AssignationContext(BaseModel):
    assignation: Assignation
    transport: ActorTransport
    _helper = None

    def __enter__(self):
        self._helper = AssignationHelper(
            assignation=self.assignation, transport=self.transport
        )

        current_assignation_helper.set(self._helper)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        current_assignation_helper.set(None)
        self._helper = None

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return self.__exit__(exc_type, exc_val, exc_tb)

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True


class ProvisionContext(BaseModel):
    provision: Provision
    transport: ActorTransport
    _helper = None

    def __enter__(self):
        self._helper = ProvisionHelper(
            provision=self.provision, transport=self.transport
        )

        current_provision_helper.set(self._helper)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        current_provision_helper.set(None)
        self._helper = None

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return self.__exit__(exc_type, exc_val, exc_tb)

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True
