"""The agent's socket control plane: what ``Init`` told us.

Registration itself is the transport handshake (``Register`` carries the agent's
declaration, ``Init`` answers it), so nothing here registers. What is left of the control
plane after that is bookkeeping: :meth:`SocketControlPlane.handle_init` records the id and
definition hash the backend assigned, which is what
:class:`~rekuest.agents.backend.SocketAgentBackend` reports.

Shelving is not here any more: the agent mints its drawers' ids itself and records them
as ``SHELVE``/``UNSHELVE`` frames, which nothing answers (``JOURNAL_ACK`` covers them).
"""

from __future__ import annotations


from arkitekt_runtime import messages
from arkitekt_runtime.agents.transport.types import MessageSink


class SocketControlPlane:
    """Init bookkeeping, over the agent's own socket."""

    def __init__(self, sink: MessageSink) -> None:
        self.sink = sink
        self.agent_id: str | None = None
        """The id the backend assigned this agent (``Init.agent``)."""
        self.server_hash: str | None = None
        """The definition hash the backend holds for this agent (``Init.hash``)."""
        self.acknowledged: bool = False
        """Whether an ``Init`` has been seen."""

    def handle_init(self, message: messages.Init) -> None:
        """Record what the backend told us about this agent."""
        self.agent_id = message.agent
        self.server_hash = message.hash
        self.acknowledged = True


__all__ = ["SocketControlPlane"]
