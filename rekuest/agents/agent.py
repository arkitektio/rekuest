"""The socket agent: the core agent, connected to a Rekuest server.

:class:`~arkitekt_runtime.agents.base.BaseAgent` runs a declared app's actions over any
transport. :class:`RekuestAgent` is that agent in distributed mode, and adds what only
means something against a Rekuest server over its websocket:

* the connection's own session bookkeeping -- the ``Init`` acknowledging ``Register``,
  ``EventAck``/``JournalAck``, and the retention and replay of what the server has not
  confirmed yet (the persist-then-ack contract);
* calls to other actions, routed through the server (:class:`~rekuest.agents.caller.AgentPostman`);
* the shelve over the socket (:class:`~rekuest.agents.backend.SocketAgentBackend`);
* rath's task scope, so rath-based service clients attribute requests to the running task.
"""

import logging
import warnings
from collections.abc import Sequence
from typing import Any, Optional

from pydantic import Field, PrivateAttr
from rath.task import task_scope

from arkitekt_runtime import messages
from arkitekt_runtime.agents.backend import LocalAgentBackend
from arkitekt_runtime.agents.base import BaseAgent
from arkitekt_runtime.agents.journal import JournalEntry
from arkitekt_spec.declare.agents.errors import AgentException
from arkitekt_spec.declare.catalogs import CatalogWarning
from rekuest.agents.backend import SocketAgentBackend
from rekuest.agents.caller import AgentPostman
from rekuest.agents.control import SocketControlPlane

logger = logging.getLogger(__name__)


class RekuestAgent(BaseAgent):
    """The core agent in distributed mode: registered with, and assigned work by, a Rekuest server."""

    task_scopes: list[Any] = Field(
        default_factory=lambda: [task_scope],
        description="Scopes entered around every assignment; rath's by default, so rath-based service clients attribute their requests to the running task.",
    )

    @property
    def control_plane(self) -> "SocketControlPlane":
        """Init bookkeeping and the shelve over this agent's socket (lazily built)."""
        if self._control_plane is None:
            from rekuest.agents.control import SocketControlPlane

            self._control_plane = SocketControlPlane(self.transport)
        return self._control_plane


    async def _aresend_unacked_reports(self) -> None:
        """Re-send terminal reports we retained but never saw acked.

        Sent as-is rather than through ``_adispatch`` so the original ``seq`` survives and
        they are not retained a second time; the backend dedups terminal reports by task.

        With a journal-acknowledging server, every journaled frame not yet covered by a
        ``JournalAck`` is re-sent too, in ``pos`` order (after the terminal reports the
        journal does not cover); the server drops duplicates by position.
        """
        retained = [self._retained[pos] for pos in sorted(self._retained)]
        covered = {message.id for message in retained}
        unacked = [
            message
            for message in self._unacked_events.values()
            if message.id not in covered
        ]
        for message in [*unacked, *retained]:
            await self.transport.asend(message)

    def _set_journal_acks(self, journal: bool) -> None:
        """Whether the server acknowledges journal positions (from its ``Init``)."""
        self._journal_acks = journal
        if not journal:
            self._retained.clear()
            self._retained_session = None

    def _retain(self, message: messages.FromAgentMessage, entry: JournalEntry) -> None:
        """Keep a journaled frame until a ``JournalAck`` covers it."""
        if self._retained_session != entry.session_id:
            self._retained.clear()
            self._retained_session = entry.session_id
        self._retained[entry.pos] = message

    def _journal_ack(self, session: str, pos: int) -> None:
        """Everything of ``session`` up to ``pos`` is persisted by the server."""
        if self._retained_session == session:
            for covered in [p for p in self._retained if p <= pos]:
                del self._retained[covered]
        for event_id, message in list(self._unacked_events.items()):
            message_pos = getattr(message, "pos", None)
            if (
                getattr(message, "journal_session", None) == session
                and message_pos is not None
                and message_pos <= pos
            ):
                del self._unacked_events[event_id]

    async def _areply_to_inquiries(
        self, inquiries: "Sequence[messages.AssignInquiry]"
    ) -> None:
        """Tell the backend which of the tasks it is asking about are still alive."""
        for inquiry in inquiries:
            await self._areport_task_liveness(inquiry.task)

    def _has_retained_terminal_report(self, task: str) -> bool:
        """Whether a terminal report for this task is still waiting to be acked."""
        return any(
            getattr(message, "task", None) == task
            for message in self._unacked_events.values()
        )

    async def _areport_task_liveness(self, task: str) -> None:
        """Report whether one task is still running on its actor."""
        if self._has_retained_terminal_report(task):
            # Inquiries arrive on the same Init that just triggered the replay of
            # retained reports, and that replay says precisely how this task ended.
            # Answering again here would only contradict it.
            return

        if task not in self.managed_assignments:
            await self._adispatch(
                messages.Critical(
                    task=task,
                    error="After disconnect actor was no longer managed (probably the app was restarted)",
                )
            )
            return

        assignment = self.managed_assignments[task]
        actor = self.managed_actors[assignment.interface]
        if await actor.acheck_task(assignment.task):
            await self._adispatch(
                messages.Progress(
                    task=task,
                    message="Actor is still running",
                    progress=0,
                )
            )
        else:
            await self._adispatch(
                messages.Critical(
                    task=task,
                    error="The assignment was not running anymore. But the actor was still managed. This could lead to some race conditions",
                )
            )

    def _process_caller_message(
        self,
        message: "messages.AssignResponse | messages.ProbeResponse | messages.ExecutionEvent",
    ) -> None:
        """Route an answer to work this agent delegated to the caller postman.

        ``ExecutionEvent`` is the base of every backend→caller ``…Event`` mirror, so an
        actor-internal ``acall``/``acall_dependency`` can observe what it delegated.
        ``ControlResponse`` is not routed: cancel/interrupt requests are fire-and-forget
        and their outcome is observed through the task's own event mirrors.
        """
        if isinstance(message, messages.AssignResponse):
            self.caller_postman.handle_assign_response(message)
        elif isinstance(message, messages.ProbeResponse):
            self.caller_postman.handle_probe_response(message)
        else:
            self.caller_postman.handle_execution_event(message)

    _control_plane: Optional["SocketControlPlane"] = PrivateAttr(default=None)
    """Init bookkeeping and the shelve over this socket, lazily built."""

    _journal_acks: bool = PrivateAttr(default=False)
    """The server persists journal positions (``Init.journal``): retain every journaled
    frame until a ``JournalAck`` covers it."""

    _retained: dict[int, messages.FromAgentMessage] = PrivateAttr(default_factory=dict)
    """Journaled frames not yet covered by a ``JournalAck``, by position."""

    _retained_session: str | None = PrivateAttr(default=None)

    _unacked_events: dict[str, messages.FromAgentMessage] = PrivateAttr(
        default_factory=dict
    )

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        """Shelve over the socket and call through it, unless the caller wired its own."""
        super().model_post_init(__context)
        if isinstance(self.backend, LocalAgentBackend):
            self.backend = SocketAgentBackend(control_plane=self.control_plane)
        if self._caller_postman is None:
            self.use_caller(AgentPostman(self.transport))

    async def _aprocess_runtime_message(self, message: messages.ToAgentMessage) -> None:
        """The socket protocol: session bookkeeping, caller answers, shelve replies."""
        if isinstance(message, messages.EventAck):
            # Backend made the reported event durable; stop retaining it.
            self._unacked_events.pop(message.event, None)
        elif isinstance(message, messages.JournalAck):
            self._journal_ack(message.journal_session, message.pos)
        elif isinstance(
            message,
            (messages.AssignResponse, messages.ProbeResponse, messages.ExecutionEvent),
        ):
            self._process_caller_message(message)
        elif isinstance(message, messages.Shelved):
            self.control_plane.handle_shelved(message)
        elif isinstance(message, messages.Unshelved):
            self.control_plane.handle_unshelved(message)
        elif isinstance(message, messages.ControlResponse):
            # Acknowledgement of a fire-and-forget cancel/interrupt request; the
            # outcome is observed through the task's own event mirrors instead.
            logger.debug(f"Ignoring control acknowledgement {message}")
        else:
            await super()._aprocess_runtime_message(message)

    def _record_init(self, message: messages.Init) -> None:
        """The server-assigned agent id, recorded before anyone waiting on ``Init`` resumes."""
        self.control_plane.handle_init(message)

    async def _aafter_init(self, message: messages.Init) -> None:
        """Every connection's ``Init`` is also the signal a drop was recovered: replay and answer."""
        self._set_journal_acks(message.journal)
        await self._aresend_unacked_reports()
        await self._areply_to_inquiries(message.inquiries)

    def _retain_emitted(
        self, message: messages.FromAgentMessage, entry: JournalEntry | None
    ) -> None:
        """Keep terminal reports until an ``EventAck``, journaled frames until a ``JournalAck``."""
        if isinstance(message, messages.TERMINAL_REPORTS):
            self._unacked_events[message.id] = message
        if entry is not None and self._journal_acks:
            self._retain(message, entry)

    async def _aresend_retained_report(self, task: str) -> bool:
        """A redelivered Assign for a finished task gets its unconfirmed report again."""
        if not self._has_retained_terminal_report(task):
            return False
        logger.warning(f"Duplicate Assign for finished task {task}; re-sending its report")
        for retained in list(self._unacked_events.values()):
            if getattr(retained, "task", None) == task:
                await self.transport.asend(retained)
        return True

    def _fail_pending_requests(self, error: Exception) -> None:
        """Fail the shelve and control requests still waiting on the server."""
        if self._control_plane is not None:
            self._control_plane.fail_pending(error)


__all__ = ["RekuestAgent"]
