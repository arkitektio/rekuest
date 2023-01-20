from pydantic import BaseModel
from rekuest.actors.base import Actor
from rekuest.api.schema import LogLevelInput, AssignationStatusInput
from rekuest.messages import Assignation, Provision
from koil import unkoil
from rekuest.agents.transport.base import AgentTransport


class AssignationHelper(BaseModel):
    assignation: Assignation
    transport: AgentTransport

    async def alog(self, level: LogLevelInput, message: str) -> None:
        await self.transport.log_to_assignation(
            id=self.assignation.assignation, level=level, message=message
        )

    def log(self, level: LogLevelInput, message: str) -> None:
        return unkoil(self.alog, level, message)

    async def aprogress(self, progress: int) -> None:
       await self.transport.change_assignation(
            id=self.assignation.assignation, status=AssignationStatusInput.PROGRESS,   progress=progress
        )

    def progress(self, progress: int) -> None:
        return unkoil(self.aprogress, progress)

    @property
    def user(self) -> str:
        return self.assignation.user

    class Config:
        arbitrary_types_allowed = True


class ProvisionHelper(BaseModel):
    provision: Provision
    transport: AgentTransport

    async def alog(self, level: LogLevelInput, message: str) -> None:
        await self.transport.log_to_provision(
            id=self.provision.provision, level=level, message=message
        )

    @property
    def guardian(self) -> str:
        return self.provision.guardian
