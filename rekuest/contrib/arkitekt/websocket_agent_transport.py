from typing import Optional
from pydantic import BaseModel
from fakts import Fakts
from herre import Herre
from rekuest.agents.transport.websocket import WebsocketAgentTransport
from pydantic import Field

from typing import Any, Awaitable, Callable, Dict, Optional

from pydantic import Field


class WebsocketAgentTransportConfig(BaseModel):
    endpoint_url: str
    instance_id: str = "default"


class ArkitektWebsocketAgentTransport(WebsocketAgentTransport):
    endpoint_url: Optional[str]
    instance_id: Optional[str]
    token_loader: Optional[Callable[[], Awaitable[str]]] = Field(exclude=True)
    fakts: Fakts
    herre: Herre
    fakts_group: str
    _old_fakt: Dict[str, Any] = {}

    def configure(self, fakt: WebsocketAgentTransportConfig) -> None:
        self.endpoint_url = fakt.endpoint_url
        self.token_loader = self.token_loader or self.herre.aget_token

    async def aconnect(self, *args, **kwargs):
        if self.fakts.has_changed(self._old_fakt, self.fakts_group):
            self._old_fakt = await self.fakts.aget(self.fakts_group)
            self.configure(WebsocketAgentTransportConfig(**self._old_fakt))

        return await super().aconnect(*args, **kwargs)
