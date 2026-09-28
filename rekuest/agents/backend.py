"""The backend of a Rekuest server, reached over the agent's own socket."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from arkitekt_spec.scalars import Identifier
from rekuest.agents.control import SocketControlPlane


class SocketAgentBackend(BaseModel):
    """The backend of a Rekuest server, reached over the agent's own socket.

    The id comes from the ``Init`` the control plane recorded; sessions are minted locally
    (the backend learns them from ``SessionInit``); the shelve is the ``Shelve`` /
    ``Unshelve`` round trips of :class:`~rekuest.agents.control.SocketControlPlane`.
    """

    control_plane: SocketControlPlane = Field(
        description="The agent's socket control plane: Init bookkeeping and the shelve.",
    )
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def registered_agent_id(self) -> str | None:
        """The server-assigned agent id, once ``Init`` has arrived."""
        return self.control_plane.agent_id

    async def acreate_session(self) -> str:
        """A fresh identifier per process; the backend learns it from ``SessionInit``."""
        return str(uuid.uuid4())

    async def ashelve(
        self,
        identifier: Identifier,
        resource_id: str,
        label: str | None = None,
        description: str | None = None,
    ) -> str:
        """Put a value on the server-side shelve and return its drawer id."""
        return await self.control_plane.ashelve(
            identifier=identifier,
            resource_id=resource_id,
            label=label,
            description=description,
        )

    async def acollect(self, key: str) -> None:
        """Release a drawer on the server."""
        await self.control_plane.aunshelve(key)
