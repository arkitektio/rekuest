"""The socket agent: the core agent, connected to a Rekuest server.

:class:`~arkitekt_runtime.agents.base.BaseAgent` runs a declared app's actions over any
transport. :class:`RekuestAgent` is that agent in distributed mode, and adds what only
means something against a Rekuest server over its websocket:

* the delivery side of the agent-report contract (rekuest's ``docs/design/journal.md``):
  every numbered frame is retained -- on disk, :mod:`rekuest.agents.retention` -- until a
  ``JOURNAL_ACK`` covers it; nothing goes out between ``REGISTER`` and ``INIT``; after
  ``INIT``, which says how far the server has each session, the rest is sent again,
  earlier sessions' first, in ``(session created, pos)`` order; a ``JOURNAL_NACK`` (a gap)
  has it sent again from the position named;
* the receiving side of the command contract: what the server sends is handled once per
  position and acknowledged (``COMMAND_ACK``), so the server can send it again after a
  dropped connection without it being run twice;
* calls to other actions, routed through the server (:class:`~rekuest.agents.caller.AgentPostman`),
  each child call taking its parent's next task step as its ``reference``;
* rath's task scope, so rath-based service clients attribute requests to the running task;
* what follows a registration (``after_registration``): rekuest's provider uploads the
  assets the app registered there, once the server knows the bloks that name them.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, ClassVar

from pydantic import Field, PrivateAttr, TypeAdapter
from rath.task import task_scope

from arkitekt_runtime import messages
from arkitekt_runtime.agents.backend import LocalAgentBackend
from arkitekt_runtime.agents.base import BaseAgent
from arkitekt_runtime.agents.journal import JournalEntry
from arkitekt_runtime.agents.transport.types import HandshakeParams
from arkitekt_spec.base import current_spec_version
from arkitekt_spec.declare.agents.errors import AgentException
from arkitekt_spec.declare.agents.types import AppContext
from rekuest.agents.backend import SocketAgentBackend
from rekuest.agents.caller import AgentPostman
from rekuest.agents.control import SocketControlPlane
from rekuest.agents.persisted_state import (
    PersistedStates,
    StoredState,
    schema_hash,
    state_store_path,
)
from rekuest.agents.retention import RetainedFrames, default_journal_path

logger = logging.getLogger(__name__)

#: Rebuilds a retained frame from what was sent.
_FRAME: TypeAdapter[messages.FromAgentMessage] = TypeAdapter(
    Annotated[messages.FromAgentMessage, Field(discriminator="type")]
)

#: A retained frame's key: its session and position.
RetainedKey = tuple[str, int]

#: How long handled commands wait to be acknowledged together.
COMMAND_ACK_DELAY = 0.05


class _OrderedSink:
    """Where the caller postman puts a message: on the agent's ordered path, so a request
    is numbered and retained like a report, behind what its task reported before it."""

    def __init__(self, agent: "RekuestAgent") -> None:
        self._agent = agent

    @property
    def connected(self) -> bool:
        return self._agent.transport.connected

    async def asend(self, message: messages.FromAgentMessage) -> None:
        await self._agent._adispatch(message)


class RekuestAgent(BaseAgent):
    """The core agent in distributed mode: registered with, and assigned work by, a Rekuest server."""

    task_scopes: list[Any] = Field(
        default_factory=lambda: [task_scope],
        description="Scopes entered around every assignment; rath's by default, so rath-based service clients attribute their requests to the running task.",
    )
    journal_path: str | None = Field(
        default_factory=default_journal_path,
        description="The SQLite file the agent keeps its unacknowledged frames in, across restarts ($REKUEST_JOURNAL_PATH, else .arkitekt/rekuest_journal.db). None keeps them in memory only.",
    )

    after_registration: Callable[[], Awaitable[None]] | None = Field(
        default=None,
        exclude=True,
        description="Run beside the agent once the server accepted its registration, and again after a reconnect if it failed. It never holds the agent back, and what it raises is logged.",
    )

    leaves_stopped_tasks_to_backend: ClassVar[bool] = True
    """The server notices that this process is gone, and does with each task it had what
    its caller asked for when it was lost: nothing is said for them at teardown."""

    holds_sends_until_init: ClassVar[bool] = True
    """Tells the transport to send nothing between ``Register`` and ``Init``: after
    ``Init`` the agent first re-sends what the server may be missing."""

    _control_plane: "SocketControlPlane | None" = PrivateAttr(default=None)
    _retained: dict[RetainedKey, messages.FromAgentMessage] = PrivateAttr(default_factory=dict)
    """Numbered frames no ``JournalAck`` covered yet, every session's."""
    _session_created: dict[str, float] = PrivateAttr(default_factory=dict)
    """When each session with retained frames was created (orders the resend)."""
    _store: RetainedFrames | None = PrivateAttr(default=None)
    _state_store: PersistedStates | None = PrivateAttr(default=None)
    """Only when a state opts in with ``persist=``."""
    _state_policies: dict[str, Any] = PrivateAttr(default_factory=dict)
    """Each persisted state's ``persist``, by state."""
    _state_hashes: dict[str, str] = PrivateAttr(default_factory=dict)
    _previous_states: dict[str, Any] = PrivateAttr(default_factory=dict)
    """What earlier runs persisted, read before this run can overwrite it."""
    _dirty_states: set[str] = PrivateAttr(default_factory=set)
    _state_flushers: list["asyncio.Task[None]"] = PrivateAttr(default_factory=list)
    _state_flush_stop: asyncio.Event | None = PrivateAttr(default=None)
    _after_registration_task: "asyncio.Task[None] | None" = PrivateAttr(default=None)
    _commands_seen: int = PrivateAttr(default=0)
    """The highest command position this process has handled."""
    _command_ack_task: "asyncio.Task[None] | None" = PrivateAttr(default=None)
    _skipped: set[tuple[str, int]] = PrivateAttr(default_factory=set)
    """The gaps already declared gone, by session and first position: said once."""

    @property
    def control_plane(self) -> "SocketControlPlane":
        """Init bookkeeping over this agent's socket (lazily built)."""
        if self._control_plane is None:
            self._control_plane = SocketControlPlane(self.transport)
        return self._control_plane

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        """Register over the socket and call through it, unless the caller wired its own."""
        super().model_post_init(__context)
        if isinstance(self.backend, LocalAgentBackend):
            self.backend = SocketAgentBackend(control_plane=self.control_plane)
        if self._caller_postman is None:
            self.use_caller(
                AgentPostman(_OrderedSink(self), child_step=self.areserve_call_step)
            )

    def _task_ended(self, task: str) -> None:
        """Its child calls' counters (the derived call keys' occurrences) go with it."""
        postman = self.caller_postman
        if isinstance(postman, AgentPostman):
            postman.forget_parent(task)

    # ------------------------------------------------------------- retention

    @property
    def retained(self) -> list[messages.FromAgentMessage]:
        """The frames no ``JournalAck`` covered yet, in the order they are re-sent."""
        return [self._retained[key] for key in self._resend_order()]

    def _resend_order(self) -> list[RetainedKey]:
        return sorted(
            self._retained,
            key=lambda key: (self._session_created.get(key[0], 0.0), key[0], key[1]),
        )

    def _open_store(self) -> RetainedFrames:
        """The on-disk store, opened on first use; loads what earlier runs left."""
        if self._store is None:
            try:
                self._store = RetainedFrames(
                    self.journal_path, agent=self.name or "", spec_version=current_spec_version()
                )
            except Exception:  # noqa: BLE001 — an unwritable path must not stop the agent
                logger.error(
                    "Could not open %s; unacknowledged frames are kept in memory only, "
                    "and will not survive a restart",
                    self.journal_path,
                    exc_info=True,
                )
                self._store = RetainedFrames(None, agent=self.name or "")
            set_aside = self._store.set_aside()
            if set_aside:
                logger.error(
                    "%d unsent frames in %s were written by another version of the spec than "
                    "this agent runs (%s). They are not sent: a server reads what this version "
                    "speaks. They stay in the file.",
                    set_aside,
                    self.journal_path,
                    current_spec_version(),
                )
            for stored in self._store.load():
                try:
                    frame = _FRAME.validate_json(stored.frame)
                except Exception:  # noqa: BLE001 — one unreadable frame must not stop the start
                    logger.error(
                        "Could not read retained frame %s/%s", stored.session_id, stored.pos,
                        exc_info=True,
                    )
                    continue
                self._session_created.setdefault(stored.session_id, stored.created_at)
                self._retained.setdefault((stored.session_id, stored.pos), frame)
            if self._retained:
                logger.info(
                    "%d frames of earlier runs are waiting to be sent", len(self._retained)
                )
        return self._store

    def _complete_resume(self, message: messages.Assign) -> messages.Assign:
        """Add the task's effects this agent still holds unsent to its resume journal.

        A process that died may have recorded a value (the clock, a model's answer) that
        never reached the server. Its successor has it in the journal on disk, and the frame
        will be resent after ``Init``: the resumed run must replay that value, not take a
        new one. Its steps count too, so none is numbered twice.
        """
        assert message.resume is not None
        self._open_store()
        known = {effect.key for effect in message.resume.effects}
        held = [
            frame
            for frame in self._retained.values()
            if isinstance(frame, messages.Effect) and frame.task == message.task and frame.key and frame.key not in known
        ]
        steps = [
            frame.task_step
            for frame in self._retained.values()
            if getattr(frame, "task", None) == message.task and getattr(frame, "task_step", None)
        ]
        if not held and not steps:
            return message
        journal = message.resume.model_copy(
            update={
                "effects": [
                    *message.resume.effects,
                    *(messages.RecordedEffect(key=f.key, effect=f.effect, value=f.value) for f in held),
                ],
                "last_step": max([message.resume.last_step, *steps]),
            }
        )
        return message.model_copy(update={"resume": journal})

    def _know_session(self, session: str) -> None:
        if session not in self._session_created:
            self._session_created[session] = self._open_store().open_session(
                session, time.time()
            )

    async def astart(self, app_context: AppContext | None = None) -> None:
        """Mint the session, and read what earlier runs could not deliver: it is sent
        first, after ``Init``."""
        await super().astart(app_context)
        self._open_store()
        self._know_session(self.current_session)
        self._open_state_store()

    def _retain_emitted(
        self, message: messages.FromAgentMessage, entry: JournalEntry | None
    ) -> None:
        """Keep every numbered frame until a ``JournalAck`` covers it, on disk too."""
        if entry is None:
            return
        self._retained[(entry.session_id, entry.pos)] = message
        try:
            self._know_session(entry.session_id)
            self._open_store().add(entry.session_id, entry.pos, message.model_dump_json())
        except Exception:  # noqa: BLE001 — memory still has it; report the loss of durability
            logger.error("Could not persist frame %s/%s", entry.session_id, entry.pos, exc_info=True)
        self._note_state_change(message)

    def _journal_ack(self, session: str, pos: int) -> None:
        """Everything of ``session`` up to ``pos`` is projected by the server."""
        for key in [k for k in self._retained if k[0] == session and k[1] <= pos]:
            del self._retained[key]
        store = self._open_store()
        try:
            store.ack(session, pos)
            if not any(key[0] == session for key in self._retained):
                store.prune(keep=self.current_session)
        except Exception:  # noqa: BLE001
            logger.error("Could not record the ack %s/%s", session, pos, exc_info=True)

    async def _aresend_retained(self) -> None:
        """Send every retained frame again, as it was, in ``(session created, pos)``
        order: earlier sessions' first, then this one's. The server skips what it has
        already projected, and acks again."""
        for key in self._resend_order():
            message = self._retained.get(key)
            if message is not None:
                await self.transport.asend(message)

    def _retained_terminal_reports(self, task: str) -> list[messages.FromAgentMessage]:
        return [
            message
            for message in self.retained
            if isinstance(message, messages.TERMINAL_REPORTS) and message.task == task
        ]

    def _has_retained_terminal_report(self, task: str) -> bool:
        """Whether a terminal report for this task is still waiting to be acked."""
        return bool(self._retained_terminal_reports(task))

    async def _aresend_retained_report(self, task: str) -> bool:
        """A redelivered Assign for a finished task gets its unconfirmed report again."""
        reports = self._retained_terminal_reports(task)
        if not reports:
            return False
        logger.warning(f"Duplicate Assign for finished task {task}; re-sending its report")
        for report in reports:
            await self.transport.asend(report)
        return True

    # ------------------------------------------------------- persisted states

    def _open_state_store(self) -> None:
        """Read what earlier runs persisted, if a state opted in, and start its flushing."""
        if self._state_store is not None:
            return
        registry = self.app_registry
        policies = {
            interface: registry.structure_registry.state_for(cls).persist
            for interface, cls in registry.state_interface_classes.items()
        }
        policies = {interface: policy for interface, policy in policies.items() if policy is not None}
        if not policies:
            return
        if self.journal_path in (None, ":memory:"):
            logger.error(
                "The states %s are declared persist=, but this agent keeps no journal file "
                "to put them beside: they are not persisted",
                sorted(policies),
            )
            return
        try:
            store = PersistedStates(state_store_path(self.journal_path), agent=self.name or "")
            stored = store.load()
        except Exception:  # noqa: BLE001 — an unwritable path must not stop the agent
            logger.error("Could not open the persisted states; they are not persisted", exc_info=True)
            return

        self._state_store = store
        self._state_policies = policies
        self._state_hashes = {
            interface: schema_hash(self._collected_state_schemas[interface]) for interface in policies
        }
        self._previous_states = {}
        for interface, state in stored.items():
            if interface not in policies:
                continue
            if state.schema_hash != self._state_hashes[interface]:
                logger.warning(
                    "The persisted state %s has other ports than the state now; starting without it",
                    interface,
                )
                continue
            self._previous_states[interface] = json.loads(state.value)

        self._state_flush_stop = asyncio.Event()
        self._state_flushers = [
            asyncio.create_task(self._aflush_state_loop(interface, policy, self._state_flush_stop))
            for interface, policy in policies.items()
            if not isinstance(policy, str)
        ]

    async def aload_previous_states(self) -> dict[str, Any]:
        """What earlier runs persisted, shrunk, of the states whose ports are unchanged."""
        return self._previous_states

    def _note_state_change(self, message: messages.FromAgentMessage) -> None:
        """A persisted state changed: write it (``"synchronous"``), or leave it to its flusher."""
        if self._state_store is None:
            return
        if isinstance(message, messages.StatePatch):
            changed = [message.state_name]
        elif isinstance(message, messages.SessionInit):
            changed = list(self._state_policies)
        else:
            # A snapshot says nothing a patch did not.
            return
        for interface in changed:
            policy = self._state_policies.get(interface)
            if policy is None:
                continue
            self._dirty_states.add(interface)
            if policy == "synchronous":
                state = self._take_dirty_state(interface)
                if state is not None:
                    self._write_state(state)

    def _take_dirty_state(self, interface: str) -> StoredState | None:
        """A state's value to write, if it changed. Serialized here, on the loop, where
        the agent changes it."""
        if interface not in self._dirty_states:
            return None
        self._dirty_states.discard(interface)
        shrunk = self._current_shrunk_states.get(interface)
        if shrunk is None:
            return None
        return StoredState(
            state_name=interface,
            schema_hash=self._state_hashes[interface],
            session_id=self.current_session,
            global_rev=self.global_revision,
            value=json.dumps(shrunk),
        )

    def _write_state(self, state: StoredState) -> None:
        store = self._state_store
        if store is None:
            return
        try:
            store.put(state)
        except Exception:  # noqa: BLE001 — the agent runs on; report the loss of durability
            logger.error("Could not persist the state %s", state.state_name, exc_info=True)

    async def _aflush_state_loop(self, interface: str, period: float, stop: asyncio.Event) -> None:
        """Write a state at most once per ``period``. Ends on ``stop``, after a last write,
        and is not cancelled: a write under way is not abandoned, to land after a newer one."""
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=period)
            state = self._take_dirty_state(interface)
            if state is not None:
                await asyncio.to_thread(self._write_state, state)

    async def _aclose_state_store(self) -> None:
        """A clean exit keeps the exact last value."""
        if self._state_flush_stop is not None:
            self._state_flush_stop.set()
        flushers, self._state_flushers = self._state_flushers, []
        if flushers:
            await asyncio.gather(*flushers, return_exceptions=True)
        store, self._state_store = self._state_store, None
        if store is not None:
            store.close()

    # ------------------------------------------------------------------ inbound

    async def aget_handshake_params(self) -> HandshakeParams:
        """Where this agent stands, with what it offers: the commands it has handled and
        the sessions it still owes frames of."""
        params = await super().aget_handshake_params()
        self._open_store()
        return params.model_copy(
            update={
                "features": [*params.features, messages.FEATURE_PROTOCOL_2],
                "commands_seen": self._commands_seen,
                "sessions": sorted({session for session, _ in self._retained}),
            }
        )

    def _is_current_init(self, message: messages.Init) -> bool:
        """An ``Init`` names the connection it answers; one of a connection gone since
        would release this connection's sends before its own arrived."""
        current = getattr(self.transport, "nonce", None)
        return message.nonce is None or current is None or message.nonce == current

    async def process(self, message: messages.ToAgentMessage) -> None:
        """Handle a command once per position, and acknowledge it."""
        pos = message.pos if isinstance(message, messages.Command) else None
        if pos is None:
            await super().process(message)
            return
        if pos <= self._commands_seen:
            logger.debug("Command %s was handled before; acknowledging it again", pos)
        else:
            try:
                await super().process(message)
            except Exception as e:  # noqa: BLE001 — one command must not end every task
                await self._aset_aside(message, pos, e)
            self._commands_seen = pos
        if self._command_ack_task is None or self._command_ack_task.done():
            self._command_ack_task = asyncio.create_task(self._aack_commands())

    async def _aset_aside(self, command: messages.Command, pos: int, error: Exception) -> None:
        """A command arrived intact and could not be handled: say so, and go on.

        Letting it end the agent would end every other task with it, and solve nothing:
        the command was not acknowledged, so the process that came next would be sent it
        again. It is acknowledged like any other, and where it was about a task this
        agent runs, that task is reported as failed, so nobody waits for it.
        """
        kind = type(command).__name__
        logger.error("Could not handle command %s (%s); it is set aside", pos, kind, exc_info=error)
        ours = (
            messages.Assign,
            messages.Cancel,
            messages.Interrupt,
            messages.Pause,
            messages.Resume,
            messages.Decide,
            messages.BlokRefused,
        )
        if isinstance(command, ours):
            await self._adispatch(
                messages.Critical(
                    task=command.task,
                    error=f"The agent could not handle {kind}: {type(error).__name__}: {error}",
                )
            )

    async def _aack_commands(self) -> None:
        """One cumulative ``COMMAND_ACK`` for what was handled meanwhile."""
        await asyncio.sleep(COMMAND_ACK_DELAY)
        await self.transport.asend(messages.CommandAck(through=self._commands_seen))

    async def _aprocess_nack(self, session: str, expected: int) -> None:
        """A gap: send again from ``expected``, or say that it is gone."""
        discard = getattr(self.transport, "discard_journaled", None)
        if discard is not None:
            discard()
        # What is still held from the position asked for on. An acknowledgement on its way
        # may not have retired the frames before it yet: they are not what this is about.
        held = sorted(pos for kept, pos in self._retained if kept == session and pos >= expected)
        if held and held[0] > expected:
            if (session, expected) in self._skipped:
                # Said once and asked again: the backend did not take it. Saying it over
                # and over would send everything again each time, with no end.
                raise AgentException(
                    f"The backend keeps asking for report {expected} of session {session}, "
                    "which this agent no longer holds and has already said is gone."
                )
            self._skipped.add((session, expected))
            logger.error(
                "The reports %s to %s of session %s are no longer held and cannot be sent again",
                expected,
                held[0] - 1,
                session,
            )
            await self.transport.asend(
                messages.JournalSkip(session=session, first=expected, last=held[0] - 1)
            )
        await self._aresend_retained()

    async def _aprocess_runtime_message(self, message: messages.ToAgentMessage) -> None:
        """The socket protocol: acks and caller answers."""
        if isinstance(message, messages.JournalAck):
            self._journal_ack(message.journal_session, message.through)
        elif isinstance(message, messages.JournalNack):
            await self._aprocess_nack(message.journal_session, message.expected)
        elif isinstance(message, messages.InquireResponse):
            postman = self.caller_postman
            if isinstance(postman, AgentPostman):
                postman.handle_inquire_response(message)
        elif isinstance(
            message,
            (messages.AssignResponse, messages.ProbeResponse, messages.StateRevisionResponse, messages.ExecutionEvent),
        ):
            self._process_caller_message(message)
        elif isinstance(message, messages.ControlResponse):
            # Acknowledgement of a fire-and-forget cancel/interrupt request; the
            # outcome is observed through the task's own event mirrors instead.
            logger.debug(f"Ignoring control acknowledgement {message}")
        else:
            await super()._aprocess_runtime_message(message)

    def _process_caller_message(
        self,
        message: "messages.AssignResponse | messages.ProbeResponse | messages.StateRevisionResponse | messages.ExecutionEvent",
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
        elif isinstance(message, messages.StateRevisionResponse):
            self.caller_postman.handle_state_revision_response(message)
        else:
            self.caller_postman.handle_execution_event(message)

    def _record_init(self, message: messages.Init) -> None:
        """The server-assigned agent id, recorded before anyone waiting on ``Init`` resumes."""
        self.control_plane.handle_init(message)

    async def _aafter_init(self, message: messages.Init) -> None:
        """Every connection's ``Init`` is also the signal a drop was recovered.

        The transport sent nothing since ``Register``. What it still queues with a
        ``pos`` is dropped there and re-sent from here, after the earlier sessions'
        frames and in order, ahead of everything new; then the transport may send.
        """
        try:
            discard = getattr(self.transport, "discard_journaled", None)
            if discard is not None:
                discard()
            for session, projected in message.positions.items():
                if projected > 0:
                    self._journal_ack(session, projected)
            await self._aresend_retained()
        finally:
            release = getattr(self.transport, "release_sends", None)
            if release is not None:
                release()
        # Neither a probe nor a question survives the connection it was sent on.
        postman = self.caller_postman
        if isinstance(postman, AgentPostman):
            postman.fail_probes()
            postman.fail_questions()
        if self.after_registration is not None and self._after_registration_task is None:
            self._after_registration_task = asyncio.create_task(
                self._arun_after_registration(self.after_registration)
            )

    async def _arun_after_registration(self, follow_up: Callable[[], Awaitable[None]]) -> None:
        """Run the follow-up once; one that failed is tried again at the next ``Init``."""
        try:
            await follow_up()
        except Exception:
            logger.exception("What follows this agent's registration failed")
            self._after_registration_task = None

    def task_status(self, task: str) -> messages.TaskStatus:
        """As the core knows it; and a task whose end is still waiting to be acknowledged has
        ended, however long ago this agent forgot the rest of it."""
        status = super().task_status(task)
        if status is messages.TaskStatus.UNKNOWN and self._has_retained_terminal_report(task):
            return messages.TaskStatus.ENDED
        return status

    async def atear_down(self) -> None:
        """Tear down, then close the store (what it holds is sent by the next run)."""
        follow_up, self._after_registration_task = self._after_registration_task, None
        acking, self._command_ack_task = self._command_ack_task, None
        for task in (follow_up, acking):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        try:
            await super().atear_down()
        finally:
            await self._aclose_state_store()
            store, self._store = self._store, None
            if store is not None:
                store.close()


__all__ = ["RekuestAgent"]
