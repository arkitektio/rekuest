from typing import Optional

from pydantic import Field

from rekuest.postmans.vars import current_postman
from koil.composition import KoiledModel


class BasePostman(KoiledModel):
    """Postman


    Postmans are the the messengers of the arkitekt platform, they are taking care
    of the communication between your app and the arkitekt-server.

    needs to implement:
        broadcast: On assignation Update or on reservation update (non updated fields are none)


    """

    async def __aenter__(self):
        current_postman.set(self)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        current_postman.set(None)
