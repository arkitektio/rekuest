"""The agent's socket control plane: what ``Init`` told us.

Registration itself is the transport handshake (``Register`` carries the agent's
declaration, ``Init`` answers it), so nothing here registers. What is left of the control
plane after that is bookkeeping: :meth:`SocketControlPlane.handle_init` records the id and
definition hash the backend assigned, which is what
:class:`~rekuest.agents.backend.SocketAgentBackend` reports.

Shelving is not here any more: the agent mints its drawers' ids itself and records them
as numbered ``SHELVE``/``UNSHELVE`` frames, which nothing answers (``JOURNAL_ACK`` covers
them). An older server's ``Shelved``/``Unshelved`` replies are ignored.
"""

from __future__ import annotations

import logging

from arkitekt_runtime import messages
from arkitekt_runtime.agents.transport.types import MessageSink

logger = logging.getLogger(__name__)


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

    def handle_shelve_reply(self, message: messages.Shelved | messages.Unshelved) -> None:
        """An older server's answer to a ``Shelve``/``Unshelve``: nothing waits on it."""
        if message.error:
            logger.debug("The server answered a shelving frame with an error: %s", message.error)


__all__ = ["SocketControlPlane"]
