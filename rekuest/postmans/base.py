from typing import Optional, List, Union, Any

from pydantic import Field

from rekuest.postmans.vars import current_postman
from rekuest.api.schema import AssignationFragment, ReserveParamsInput
from koil.composition import KoiledModel
import asyncio


class BasePostman(KoiledModel):
    """Postman


    Postmans are the schedulers of the arkitekt platform, they are taking care
    of the communication between your app and the arkitekt-server. And
    provide abstractions to map the asynchornous message-based nature of the arkitekt-server to
    the (a)sync nature of your app. It maps assignations to functions or generators
    depending on the definition, to mimic an actor-like behaviour.

    """

    connected = Field(default=False)

    async def aassign(
        self,
        reservation: str,
        args: List[Any],
        persist=True,
        log=False,
        reference: str = None,
        parent: Union[AssignationFragment, str] = None,
    ) -> asyncio.Queue:
        ...

    async def aunassign(
        self,
        assignation: str,
    ) -> AssignationFragment:
        ...

    async def areserve(
        self,
        node: str,
        params: ReserveParamsInput = None,
        provision: str = None,
        reference: str = "default",
    ) -> asyncio.Queue:
        ...

    async def aunreserve(self, reservation_id: str):
        ...
