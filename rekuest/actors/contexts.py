from dataclasses import dataclass
from rekuest.messages import Assignation, Provision
from rekuest.actors.helper import AssignationHelper, ProvisionHelper
from rekuest.actors.base import Actor
from rekuest.actors.vars import current_assignation_helper, current_provision_helper
from rekuest.agents.transport.base import AgentTransport


@dataclass
class AssignationContext:
    assignation: Assignation
    transport: AgentTransport
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


@dataclass
class ProvisionContext:
    provision: Provision
    transport: AgentTransport
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
