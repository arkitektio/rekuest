from typing import Optional

from pydantic import Field

from rekuest.postmans.vars import current_postman
from koil.composition import KoiledModel


class BasePostman(KoiledModel):
    """Postman


    Postmans are the schedulers of the arkitekt platform, they are taking care
    of the communication between your app and the arkitekt-server. And
    provide abstractions to map the asynchornous message-based nature of the arkitekt-server to
    the (a)sync nature of your app. It maps assignations to functions or generators
    depending on the definition, to mimic an actor-like behaviour.

    """

    connected = Field(default=False)
