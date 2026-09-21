"""The agent's socket control plane: what ``Init`` told us, and the shelve.

Registration itself is the transport handshake (``Register`` carries the agent's
declaration, ``Init`` answers it), so nothing here registers. What is left of the control
plane after that is bookkeeping and the shelve:

- :meth:`SocketControlPlane.handle_init` records the id and definition hash the backend
  assigned, which is what :class:`~rekuest.agents.backend.SocketAgentBackend` reports.
- :meth:`SocketControlPlane.ashelve` is a request/reply round trip (``Shelve`` →
  ``Shelved``), correlated by a client-minted ``ref`` and bounded by a timeout, so an actor
  shrinking a memory structure never hangs on a lost reply.
- :meth:`SocketControlPlane.aunshelve` is send-only. Its one caller answers a ``Collect``
  from inside the agent's message loop, and awaiting a reply there would deadlock the loop
  that delivers it; an ``Unshelved`` error is logged, because the local drawer is gone
  either way.

A sibling of :class:`~rekuest.agents.caller.AgentPostman`, not an extension: the postman is
the caller surface handed to actor code, and shelving is not something an actor calls.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from rekuest import messages
from rekuest.agents.errors import AgentException
from rekuest.agents.transport.types import MessageSink
from rekuest.scalars import Identifier

logger = logging.getLogger(__name__)


class SocketControlPlane:
    """Init bookkeeping and the shelve, over the agent's own socket."""

    def __init__(self, sink: MessageSink, reply_timeout: float = 30.0) -> None:
        self.sink = sink
        self.reply_timeout = reply_timeout
        self.agent_id: str | None = None
        """The id the backend assigned this agent (``Init.agent``)."""
        self.server_hash: str | None = None
        """The definition hash the backend holds for this agent (``Init.hash``)."""
        self.acknowledged: bool = False
        """Whether an ``Init`` has been seen."""
        self._pending: dict[str, asyncio.Future[messages.Shelved]] = {}

    # ---------------------------------------------------------------- inbound

    def handle_init(self, message: messages.Init) -> None:
        """Record what the backend told us about this agent."""
        self.agent_id = message.agent
        self.server_hash = message.hash
        self.acknowledged = True

    def handle_shelved(self, message: messages.Shelved) -> None:
        """Resolve the ``Shelve`` this answers."""
        future = self._pending.pop(message.ref, None)
        if future is None or future.done():
            logger.warning("Shelved %s answers no pending Shelve", message.ref)
            return
        future.set_result(message)

    def handle_unshelved(self, message: messages.Unshelved) -> None:
        """An ``Unshelve`` was answered; nothing waits on it, an error is only logged."""
        if message.error:
            logger.warning("The backend could not unshelve a drawer: %s", message.error)

    def fail_pending(self, error: BaseException) -> None:
        """Fail every waiter, when the agent is torn down."""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    # --------------------------------------------------------------- outbound

    async def ashelve(
        self,
        identifier: Identifier,
        resource_id: str,
        label: str | None = None,
        description: str | None = None,
    ) -> str:
        """Put a value on the backend's shelve; the drawer id it got."""
        ref = str(uuid.uuid4())
        future: asyncio.Future[messages.Shelved] = asyncio.get_running_loop().create_future()
        self._pending[ref] = future
        try:
            await self.sink.asend(
                messages.Shelve(
                    ref=ref,
                    identifier=str(identifier),
                    resource_id=resource_id,
                    label=label,
                    description=description,
                )
            )
            shelved = await asyncio.wait_for(future, self.reply_timeout)
        except TimeoutError:
            raise AgentException(
                f"The backend did not answer the shelve request within {self.reply_timeout}s"
            ) from None
        finally:
            self._pending.pop(ref, None)
        if shelved.error or shelved.drawer is None:
            raise AgentException(f"The backend refused to shelve the value: {shelved.error}")
        return shelved.drawer

    async def aunshelve(self, drawer: str) -> None:
        """Tell the backend a drawer is gone. Send-only, see the module docstring."""
        await self.sink.asend(messages.Unshelve(drawer=drawer))


__all__ = ["SocketControlPlane"]
