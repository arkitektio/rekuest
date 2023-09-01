from pydantic import BaseModel
from rekuest.api.schema import LogLevelInput, AssignationStatusInput
from rekuest.messages import Assignation, Provision
from koil import unkoil
from rekuest.actors.types import Assignment
from rekuest.actors.transport.types import ActorTransport, AssignTransport, Passport


class AssignationHelper(BaseModel):
    passport: Passport
    assignment: Assignment
    transport: AssignTransport

    async def alog(self, level: LogLevelInput, message: str) -> None:
        await self.transport.log(level=level, message=message)

    async def aprogress(self, progress: int) -> None:
        await self.transport.change(
            status=AssignationStatusInput.PROGRESS,
            progress=progress,
        )

    def progress(self, progress: int) -> None:
        return unkoil(self.aprogress, progress)

    def log(self, level: LogLevelInput, message: str) -> None:
        return unkoil(self.alog, level, message)

    @property
    def user(self) -> str:
        return self.assignment.user

    @property
    def assignation(self) -> str:
        """Returns the governing assignation that cause the chained that lead to this execution"""
        return self.assignment.assignation

    class Config:
        arbitrary_types_allowed = True


class ProvisionHelper(BaseModel):
    provision: Provision
    transport: ActorTransport

    async def alog(self, level: LogLevelInput, message: str) -> None:
        await self.transport.log(level=level, message=message)

    @property
    def guardian(self) -> str:
        return self.provision.guardian

    class Config:
        arbitrary_types_allowed = True
