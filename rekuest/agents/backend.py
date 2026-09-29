"""The backend of a Rekuest server, reached over the agent's own socket."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from rekuest.agents.control import SocketControlPlane


class SocketAgentBackend(BaseModel):
    """The backend of a Rekuest server, reached over the agent's own socket.

    The id comes from the ``Init`` the control plane recorded; sessions are minted locally
    (the backend learns them from ``SessionInit``). Shelving is the agent's own.
    """

    control_plane: SocketControlPlane = Field(
        description="The agent's socket control plane: Init bookkeeping.",
    )
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def registered_agent_id(self) -> str | None:
        """The server-assigned agent id, once ``Init`` has arrived."""
        return self.control_plane.agent_id

    async def acreate_session(self) -> str:
        """A fresh identifier per process; the backend learns it from ``SessionInit``."""
        return str(uuid.uuid4())
