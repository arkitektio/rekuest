"""Base agent class

This is the base class for all agents. It provides the basic functionality
for managing the lifecycle of the actors that are spawned from it.

"""

import hashlib
import json

import asyncio
import contextlib
import copy
import logging
import time
import uuid
import warnings
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Self,
)
from collections.abc import AsyncIterator, Awaitable, Sequence
import janus
import jsonpatch  # type: ignore[import-untyped]
from pydantic import ConfigDict, Field, PrivateAttr

from contextlib import AbstractContextManager, nullcontext
from rekuest.agents.types import BoundApp
from koil.composition import KoiledModel
from rekuest import messages
from rekuest.actors.types import Actor
from rekuest.agents.errors import (
    AgentException,
    MissingServiceWarning,
    ProvisionException,
)
from rekuest.agents.dataclasses import QueuedPatchEvent, RevisedState
from rekuest.agents.types import AppContext, T
from rekuest.agents.policy import ConnectionPolicy
from rekuest.agents.hooks.registry import (
    ShutdownHook,
    StartupHook,
    StartupHookReturns,
)
from rekuest.agents.lock import TaskLock
from rekuest.app import AppRegistry
from rekuest.agents.transport.types import AgentTransport, HandshakeParams
from rekuest.catalogs import CatalogWarning
from rekuest.agents.backend import (
    AgentBackend,
    LocalAgentBackend,
    SocketAgentBackend,
)
from rekuest.protocol.schema import StateDefinitionInput
from rekuest.protocol.types import AnyState
from rekuest.scalars import Identifier
from rekuest.state.observable import Mutation, adopt, evented
from rekuest.state.write import write_view
from rekuest.state.publish import Patch
from rekuest.state.readonly import read_only_view
from rekuest.state.shrink import ashrink_state
from rekuest.structures.registry import StructureRegistry
from rekuest.structures.serialization.actor import ashrink_return
from rekuest.structures.types import JSONSerializable

logger = logging.getLogger(__name__)

# How many finished task ids an agent remembers, to recognise a redelivered Assign for work
# that already ran. Redeliveries arrive within the backend's pickup deadline (~a minute), so
# this only has to cover the tasks that can finish in that time.
_FINISHED_TASKS_REMEMBERED = 2048

# ``AppContext``/``RevisedState``/``QueuedPatchEvent`` used to be defined here;
# they stay importable from this module.
__all__ = [
    "BaseAgent",
    "RekuestAgent",
    "AppContext",
    "T",
    "QueuedPatchEvent",
    "RevisedState",
]


if TYPE_CHECKING:
    from rekuest.agents.caller import AgentPostman
    from rekuest.agents.control import SocketControlPlane


class BaseAgent(KoiledModel):
    """Agent

    Agents are the governing entities for every app. They are responsible for
    managing the lifecycle of the direct actors that are spawned from them through arkitekt.

    Agents are nothing else than actors in the classic distributed actor model, but they are
    always provided when the app starts and they do not provide functionality themselves but rather
    manage the lifecycle of the actors that are spawned from them.

    The actors that are spawned from them are called guardian actors and they are the ones that+
    provide the functionality of the app. These actors can then in turn spawn other actors that
    are not guardian actors. These actors are called non-guardian actors and their lifecycle is
    managed by the guardian actors that spawned them. This allows for a hierarchical structure
    of actors that can be spawned from the agents.


    """

    name: str | None = Field(
        default=None,
        description="The name of the agent. This is used to identify the agent in the system.",
    )

    # TODO: KV Store
    shelve: dict[str, Any] = Field(default_factory=dict)  # kv_store -> Seperate
    transport: AgentTransport
    backend: AgentBackend = Field(
        default_factory=LocalAgentBackend,
        description="Where this agent registers itself, mints sessions and shelves values. Off the message socket; see rekuest.agents.backend.",
    )
    app_registry: AppRegistry = Field(default_factory=AppRegistry)
    bound_app: BoundApp | None = Field(
        default=None,
        exclude=True,
        description="The running app this agent belongs to: it answers get(cls) with the app's clients (arkitekt's Runtime), which are injected into parameters annotated with a client class. None for an agent that runs on its own.",
    )

    contexts: dict[str, Any] = Field(
        default_factory=dict,
        description="Maps context keys to context values registed with @context",
    )
    states: dict[str, AnyState] = Field(
        default_factory=dict,
        description="Maps the state key to the state value. This is used to store the states of the agent.",
    )
    locks: dict[str, TaskLock] = Field(default_factory=dict)

    capture_condition: asyncio.Condition = Field(default_factory=asyncio.Condition)
    capture_active: bool = Field(default=False)

    managed_actors: dict[str, Actor] = Field(default_factory=dict)

    managed_assignments: dict[str, messages.Assign] = Field(default_factory=dict)
    running_assignments: dict[str, str] = Field(
        default_factory=dict, description="Maps task to actor id"
    )

    _current_shrunk_states: dict[str, JSONSerializable] = PrivateAttr(
        default_factory=lambda: {}  # type: ignore[return-value]
    )
    _app_context: AppContext | None = PrivateAttr(default=None)
    _caller_postman: Optional["AgentPostman"] = PrivateAttr(default=None)
    """The agent-as-caller postman, lazily built. :class:`~rekuest.task.Task` and the
    dependency proxies call through it, so their calls originate over this socket."""
    _control_plane: Optional["SocketControlPlane"] = PrivateAttr(default=None)
    """Init bookkeeping and the shelve over this socket, lazily built."""

    _connected_event: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
    """Set when the server acknowledges the agent (an ``Init`` message is received)."""
    _receiver: AsyncIterator[messages.ToAgentMessage] | None = PrivateAttr(
        default=None
    )
    """The live transport message stream, shared between ``aconnect`` and ``aloop``."""
    _consume_task: asyncio.Task[None] | None = PrivateAttr(default=None)
    """The task consuming ``_receiver``: started by ``aconnect`` (the activation that
    follows ``Init`` needs socket replies), adopted by ``aloop``, stopped by teardown."""
    _provider_ready: bool = PrivateAttr(default=True)
    """Whether work the backend assigns can run. False between the socket opening and
    the end of activation: the backend may dispatch as soon as it has sent ``Init``, but
    the states and contexts an actor needs exist only once activation is through."""
    _held_provider_messages: list[messages.ToAgentMessage] = PrivateAttr(
        default_factory=list
    )
    """Provider messages that arrived before activation, replayed in order after it."""

    _interface_stateschema_input_map: dict[str, StateDefinitionInput] = PrivateAttr(
        default_factory=lambda: {}  # typ
    )

    _disconnect_watchdog_task: asyncio.Task[None] | None = PrivateAttr(default=None)
    _background_tasks: dict[str, asyncio.Task[None]] = PrivateAttr(
        default_factory=lambda: {}
    )
    _collected_state_schemas: dict[str, StateDefinitionInput] = PrivateAttr(
        default_factory=lambda: {}
    )
    _collected_startup_hooks: dict[str, StartupHook] = PrivateAttr(
        default_factory=lambda: {}
    )
    _collected_shutdown_hooks: dict[str, ShutdownHook] = PrivateAttr(
        default_factory=lambda: {}
    )
    _collected_background_workers: dict[str, Any] = PrivateAttr(
        default_factory=lambda: {}
    )
    _ran_startup_hooks: bool = PrivateAttr(default=False)
    """Set once the startup hooks have run, so teardown only runs the shutdown hooks
    for an agent that actually started (and only once per start)."""

    # Event based necessities
    force: bool | None = Field(
        default=None,
        description="Kick any connection already registered for this agent and take over. None defers to the transport's own build-time policy.",
    )
    current_session: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="A unique identifier for the current session. This is used to group patches and snapshots that belong to the same logical session together. By default an agent start a new session when booting up",
    )
    _event_queue: janus.Queue[QueuedPatchEvent] | None = PrivateAttr(default=None)
    _patch_processor_task: asyncio.Task[None] | None = PrivateAttr(default=None)
    _event_seq: int = PrivateAttr(default=0)
    # message id -> retained terminal event awaiting its EventAck (insertion-ordered dict)
    _unacked_events: dict[str, messages.FromAgentMessage] = PrivateAttr(
        default_factory=dict
    )
    # Recently finished task ids (insertion-ordered, bounded): lets a redelivered Assign for
    # work that already ran be recognised after ``managed_assignments`` has forgotten it.
    _finished_tasks: dict[str, None] = PrivateAttr(default_factory=dict)
    global_revision: int = 0
    snapshot_interval: int = Field(
        default=60,
        description="How many persisted patches should elapse before all current shrunk states are checkpointed.",
    )
    teardown_join_timeout: float = Field(
        default=5.0,
        description="Maximum seconds to wait for queued state patches to flush during teardown before closing the patch queue anyway. Bounds teardown so it can never hang on an unconsumed patch.",
    )
    actor_cancel_timeout: float = Field(
        default=5.0,
        description="Maximum seconds to wait for one actor to cancel its in-flight assignments during teardown before abandoning it. Bounds teardown so an actor that swallows cancellation cannot hang it.",
    )
    cancel_grace_period: float = Field(
        default=5.0,
        description="Maximum seconds to wait for the message-consumer task to unwind when the agent loop is cancelled before proceeding to teardown anyway. Bounds cancellation so it can never hang on a stream that ignores cancellation.",
    )
    connection_policy: ConnectionPolicy = Field(
        default_factory=ConnectionPolicy,
        description="How hard this agent fights to keep its control channel: retry budget, backoff, and when to stop trying. Handed to the transport, which executes it.",
    )
    shutdown_hook_timeout: float = Field(
        default=20.0,
        description="Maximum seconds a single shutdown hook may run during teardown before it is abandoned. Bounds teardown so it can never hang on a hook that does not return.",
    )
    running: bool = False
    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def caller_postman(self) -> "AgentPostman":
        """The agent-as-caller postman (lazily built).

        :class:`~rekuest.task.Task` and the dependency proxies call through it, so their
        calls originate over this agent's socket instead of through the GraphQL postman.
        """
        if self._caller_postman is None:
            from rekuest.agents.caller import AgentPostman

            self._caller_postman = AgentPostman(self.transport)
        return self._caller_postman

    @property
    def control_plane(self) -> "SocketControlPlane":
        """Init bookkeeping and the shelve over this agent's socket (lazily built)."""
        if self._control_plane is None:
            from rekuest.agents.control import SocketControlPlane

            self._control_plane = SocketControlPlane(self.transport)
        return self._control_plane

    async def alock(self, key: str, task: str) -> None:
        """Tell the backend a task has acquired a lock.

        These were silent no-ops on this class, so only the FastAPI agent actually
        reported locks; ``Lock``/``Unlock`` are part of the protocol for every agent.

        Best-effort on purpose: mutual exclusion is provided by the local
        :class:`~rekuest.agents.lock.TaskLock`, and this only *tells* the backend
        about it. A lock must still be acquirable when the reporting channel is down, so a
        failed report is logged rather than raised — otherwise an unreachable backend would
        stop the app from running work it can serialise perfectly well on its own.
        """
        await self._areport_lock(messages.Lock(key=key, task=task))

    async def aunlock(self, key: str) -> None:
        """Tell the backend a task has released a lock. Best-effort, as :meth:`alock`."""
        await self._areport_lock(messages.Unlock(key=key))

    async def _areport_lock(self, message: "messages.Lock | messages.Unlock") -> None:
        """Send a lock report, logging rather than raising if it cannot go out."""
        try:
            await self.transport.asend(message)
        except Exception:
            logger.warning(
                "Failed to report %s for key %s",
                type(message).__name__,
                message.key,
                exc_info=True,
            )

    async def aget_read_only_proxy(self, key: str) -> AnyState:
        """Acquire a state for a given key, for an actor that only reads it.

        Returns a live view that refuses writes, so declaring a state read-only actually
        means something. See :mod:`rekuest.state.readonly` for what the view does and
        does not cover.
        """
        state = self.states[key]
        view = read_only_view(state, key)
        if view is state:
            logger.debug(
                "State %s cannot be given a read-only view (no instance dict); "
                "handing back the writeable state",
                key,
            )
        return view

    async def aget_write_proxy(
        self, key: str, mutation: Mutation | None = None
    ) -> AnyState:
        """Acquire a state for a given key, for an actor that writes to it.

        With a ``mutation`` (a task's: its id and the locks it holds) the state is
        handed out as that task's write view, so its changes are attributed to it.
        """
        state = self.states[key]
        return write_view(state, mutation) if mutation is not None else state

    async def apatch_event_loop(self) -> None:
        """Patch the event loop to process queued patches in order."""
        if self._event_queue is None:
            raise AgentException("Patch queue is not initialized")

        try:
            logger.debug("Starting patch event loop")
            while True:
                queued_patch = await self._event_queue.async_q.get()
                try:
                    await self._aprocess_patch_event(queued_patch)
                finally:
                    self._event_queue.async_q.task_done()
        except asyncio.CancelledError:
            logger.debug("Patch event loop cancelled, shutting down")
            raise

    def publish_patch(self, interface: str, patch: Patch) -> None:
        """Publish a patch to the agent. This is used to publish patches to the
        agent from the actor."""

        if self._event_queue is None:
            raise AgentException("Patch queue is not initialized")
        self._event_queue.sync_q.put(QueuedPatchEvent(interface=interface, patch=patch))

    async def _aprocess_patch_event(self, queued_patch: QueuedPatchEvent) -> None:
        interface = queued_patch.interface
        patch = queued_patch.patch

        # Check the revisions of the state
        future_global_rev = self.global_revision + 1

        # Enforce that patches are applied in order
        if interface not in self._current_shrunk_states:
            self._current_shrunk_states[interface] = await self.ashrink_state(
                interface=interface,
                state=self.states[interface],
            )

        shrunk_value = await self._ashrink_patch_value(interface, patch)

        self._aapply_patch_to_shrunk_state(interface, patch, shrunk_value)

        self.global_revision = future_global_rev
        if self.global_revision % self.snapshot_interval == 0:
            await self.apublish_snapshot(
                messages.StateSnapshot(
                    session_id=self.current_session,
                    global_rev=self.global_revision,
                    snapshots={
                        interface: copy.deepcopy(shrunk_state)
                        for interface, shrunk_state in self._current_shrunk_states.items()
                    },
                )
            )

        await self.apublish_patch(
            messages.StatePatch(
                global_rev=self.global_revision,
                state_name=interface,
                ts=queued_patch.event_time.timestamp(),
                op=patch.op,
                path=patch.path,
                value=shrunk_value,
                old_value=None,
                task_id=patch.correlation_id,
                session_id=self.current_session,
            ),
        )

    async def _ashrink_patch_value(
        self, interface: str, patch: Patch
    ) -> JSONSerializable | None:
        if patch.op not in ("add", "replace"):
            return None

        if patch.port is None:
            raise AgentException(f"No state schema found for interface {interface}")

        structure_registry = self.get_structure_registry_for_interface(interface)
        return await ashrink_return(patch.port, patch.value, structure_registry, self)

    def _aapply_patch_to_shrunk_state(
        self,
        interface: str,
        patch: Patch,
        shrunk_value: JSONSerializable | None,
    ) -> None:
        patch_document: dict[str, JSONSerializable] = {
            "op": patch.op,
            "path": patch.path,
        }
        if patch.op != "remove":
            patch_document["value"] = shrunk_value

        jsonpatch.apply_patch(
            self._current_shrunk_states[interface],
            [patch_document],
            in_place=True,
        )

    async def acollect(self, key: str) -> None:
        """Drop a local drawer and release it on the backend.

        The local drop is what matters; a release the backend cannot take is logged, not
        raised, so it never takes the message loop down with it.
        """
        del self.shelve[key]
        try:
            await self.backend.acollect(key)
        except Exception:
            logger.warning("Could not release drawer %s on the backend", key, exc_info=True)

    async def _acreate_session(self) -> str:
        """Mint the identifier for this run, however the backend does that."""
        return await self.backend.acreate_session()

    def get_locks_for_keys(self, keys: Sequence[str]) -> list[TaskLock]:
        """Get the locks for the given keys.

        Args:
            keys: The keys to get the locks for.
        Returns:
            The list of locks for the given keys.
        """
        return [self.locks[key] for key in keys if key in self.locks]

    def collect_from_registry(self) -> None:
        """Collect state schemas, hooks, sync keys and locks from the app registry.

        The actual implementation/state/blok payload is assembled (and validated)
        lazily by ``AppRegistry.to_implement_agent_input`` at registration time; this
        only populates the runtime bookkeeping the agent needs while running.
        """
        app_registry = self.app_registry

        # Collect state schemas
        for interface, schema in app_registry.states.items():
            self._collected_state_schemas[interface] = schema.definition

        # Collect startup hooks, shutdown hooks and background workers
        for name, hook in app_registry.hooks_registry.startup_hooks.items():
            self._collected_startup_hooks[name] = hook
        for name, shutdown_hook in app_registry.hooks_registry.shutdown_hooks.items():
            self._collected_shutdown_hooks[name] = shutdown_hook
        for name, worker in app_registry.hooks_registry.background_worker.items():
            self._collected_background_workers[name] = worker

        # Build the runtime task locks
        for lock_schema in app_registry.get_locks():
            if lock_schema.key not in self.locks:
                self.locks[lock_schema.key] = TaskLock(self, lock_schema)

        self._warn_about_missing_services()

    def _warn_about_missing_services(self) -> None:
        """Warn when a registered function uses a structure of a service the app lacks.

        Only meaningful for an agent bound to an app: that is the only case where
        the set of services is known. Caught here rather than at the first call,
        where it would surface as a failed expansion far from its cause.
        """
        if self.bound_app is None:
            return
        for service, interfaces in sorted(self.app_registry.required_services().items()):
            if service not in self.bound_app.services:
                warnings.warn(
                    f"{', '.join(sorted(interfaces))} use structures of the "
                    f"'{service}' service, but this app was built without it. "
                    f"Add '{service}' to the app's services to expand them.",
                    MissingServiceWarning,
                    stacklevel=2,
                )

    def get_structure_registry_for_interface(self, interface: str) -> StructureRegistry:
        """Get the structure registry for a given interface from the app registry.

        Args:
            interface: The interface to get the registry for.

        Returns:
            The structure registry for the interface.
        """

        try:
            return self.app_registry.get_registry_for_interface(interface)
        except (KeyError, AssertionError):
            raise AgentException(
                f"No structure registry found for interface {interface}"
            )

    async def ashelve(
        self,
        identifier: Identifier,
        resource_id: str,
        label: str | None = None,
        description: str | None = None,
    ) -> str:
        """Put a value on the backend's shelve and return its drawer key."""
        return await self.backend.ashelve(
            identifier=identifier,
            resource_id=resource_id,
            label=label,
            description=description,
        )

    async def aput_on_shelve(
        self,
        identifier: Identifier,
        value: Any,  # noqa: ANN401
    ) -> str:  # noqa: ANN401
        """Get the shelve for the agent. This is used to get the shelve
        for the agent and all the actors that are spawned from it.
        """

        if hasattr(value, "aget_label"):
            label = await value.aget_label()
        else:
            label = None

        if hasattr(value, "aget_description"):
            description = await value.aget_description()
        else:
            description = None

        if not label:
            label = str(value)

        drawer_id = await self.ashelve(
            identifier=identifier,
            resource_id=uuid.uuid4().hex,
            label=label,
            description=description,
        )

        self.shelve[drawer_id] = value

        return drawer_id

    async def aget_from_shelve(self, key: str) -> Any:  # noqa: ANN401
        """Get a value from the shelve. This is used to get values from the
        shelve for the agent and all the actors that are spawned from it.
        """
        assert key in self.shelve, "Drawer is not in current shelve"
        return self.shelve[key]

    async def process(self, message: messages.ToAgentMessage) -> None:
        """Route one inbound message to the concern that owns it.

        The four groups below are the actual seams in this protocol, and keeping them
        apart is what stops connection bookkeeping, provider work and caller work from
        being read as one thing:

        * **session** — the connection's own bookkeeping: the ``Init`` acknowledging our
          ``Register``, and the ``EventAck`` that ends a report's retention.
        * **provider** — work the backend assigns *to* this agent, routed to actors.
        * **caller** — answers to work this agent delegated, routed to the caller postman.
        * **shelve** — local memory the backend asks us to release, and its answers to
          what we shelved.
        """
        logger.info(f"Agent received {message}")

        if isinstance(
            message, (messages.Init, messages.EventAck, messages.ProtocolError)
        ):
            await self._aprocess_session_message(message)
        elif isinstance(
            message,
            (
                messages.Assign,
                messages.Cancel,
                messages.Interrupt,
                messages.Pause,
                messages.Resume,
            ),
        ):
            await self._aprocess_provider_message(message)
        elif isinstance(
            message,
            (
                messages.AssignResponse,
                messages.ProbeResponse,
                messages.ExecutionEvent,
            ),
        ):
            self._process_caller_message(message)
        elif isinstance(message, messages.Collect):
            for key in message.drawers:
                await self.acollect(key)
        elif isinstance(message, messages.Shelved):
            self.control_plane.handle_shelved(message)
        elif isinstance(message, messages.Unshelved):
            self.control_plane.handle_unshelved(message)
        elif isinstance(message, messages.ControlResponse):
            # Acknowledgement of a fire-and-forget cancel/interrupt request; the
            # outcome is observed through the task's own event mirrors instead.
            logger.debug(f"Ignoring control acknowledgement {message}")
        else:
            raise AgentException(f"Unknown message type {type(message)}")

    # ---------------------------------------------------------------- session

    async def _aprocess_session_message(
        self,
        message: "messages.Init | messages.EventAck | messages.ProtocolError",
    ) -> None:
        """Handle the connection's own bookkeeping."""
        if isinstance(message, messages.Init):
            # Init is the server's acknowledgement of our Register -- and of the
            # declaration it carried, so this is where the agent is registered. It
            # releases anyone waiting in aconnect(). The backend re-sends it on every
            # connection, which is also the only signal we get that a drop was recovered.
            self.control_plane.handle_init(message)
            for diagnostic in message.diagnostics:
                where = f" (at {diagnostic.path})" if diagnostic.path else ""
                warnings.warn(
                    f"{diagnostic.code}: {diagnostic.message}{where}",
                    CatalogWarning,
                    stacklevel=2,
                )
            self._connected_event.set()
            await self._aresend_unacked_reports()
            await self._areply_to_inquiries(message.inquiries)
        elif isinstance(message, messages.EventAck):
            # Backend made the reported event durable; stop retaining it.
            self._unacked_events.pop(message.event, None)
        elif not self._connected_event.is_set():
            # Before Init, a protocol error is the backend refusing our Register: the
            # declaration did not fit (catalog mismatch, ownership conflict), or the
            # backend predates socket registration and rejects the fields it carries.
            raise AgentException(
                f"The backend refused this agent's registration: {message.error}"
            )
        else:
            raise AgentException(
                "Received a protocol error from the backend. This usually means "
                f"the agent sent a message the backend could not process: {message.error}"
            )

    async def _aresend_unacked_reports(self) -> None:
        """Re-send terminal reports we retained but never saw acked.

        Sent as-is rather than through ``_adispatch`` so the original ``seq`` survives and
        they are not retained a second time; the backend dedups terminal reports by task.
        """
        for retained in list(self._unacked_events.values()):
            await self.transport.asend(retained)

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

    # --------------------------------------------------------------- provider

    async def _aprocess_provider_message(
        self,
        message: "messages.Assign | messages.Cancel | messages.Interrupt | messages.Pause | messages.Resume",
    ) -> None:
        """Route work the backend assigned to this agent to the actor running it.

        Held until activation is through: the backend may dispatch as soon as it has
        acknowledged the agent, but the states and contexts an actor needs come from the
        startup hooks that run after ``Init``.
        """
        if not self._provider_ready:
            self._held_provider_messages.append(message)
            return
        await self._arun_provider_message(message)

    async def _arun_provider_message(
        self,
        message: "messages.Assign | messages.Cancel | messages.Interrupt | messages.Pause | messages.Resume",
    ) -> None:
        if isinstance(message, messages.Assign):
            await self._aassign_to_actor(message)
        else:
            await self._aforward_to_running_actor(message)

    async def _arelease_held_provider_messages(self) -> None:
        """Run what the backend assigned during activation, in arrival order, then open up.

        Still holding while replaying, so a message arriving mid-replay queues behind the
        held ones instead of overtaking them.
        """
        while self._held_provider_messages:
            message = self._held_provider_messages.pop(0)
            await self._arun_provider_message(message)  # type: ignore[arg-type]
        self._provider_ready = True

    async def _aassign_to_actor(self, message: messages.Assign) -> None:
        """Hand a new assignment to its actor, spawning the actor if needed.

        A failure anywhere here is reported as ``Critical`` against the task, so the backend
        learns the assignment died rather than waiting on it. It is NOT re-raised: the failure
        belongs to this one assignment (unknown interface, an actor that would not spawn), and
        propagating it tears the whole agent down — abandoning every other assignment it is
        running over a problem that has already been reported.

        Assigns are delivered at-least-once: the backend redelivers one it got no report for
        (its pickup watchdog), and recovers frames a dying connection had popped but not acked.
        So the same task id may arrive twice, and running it twice is never what anyone meant.
        """
        if await self._ais_duplicate_assign(message):
            return
        try:
            actor = self.managed_actors.get(message.interface)
            if actor is None:
                # aspawn_actor_from_assign records the assignment itself.
                actor = await self.aspawn_actor_from_assign(message)
            else:
                self.managed_assignments[message.task] = message
            self.running_assignments[message.task] = actor.id
            await actor.apass(message)
        except Exception as e:
            await self._adispatch(
                messages.Critical(
                    task=message.task,
                    error=f"Not able to create actor for interface {message.interface}: {e}",
                )
            )
            logger.error(
                f"Could not start assignment {message.task} on {message.interface}",
                exc_info=True,
            )

    async def _ais_duplicate_assign(self, message: messages.Assign) -> bool:
        """Whether this Assign is a redelivery of a task we already have — and answer it.

        * still running → a ``Log`` tells the backend the task *was* picked up (any report
          does; a ``Progress`` would reset the progress the user sees);
        * finished, report not yet acked → the retained terminal report is re-sent, which is
          exactly what the backend is missing;
        * finished and acked → the backend already has the outcome; nothing to say.
        """
        task = message.task
        if task in self.managed_assignments:
            logger.warning(f"Ignoring duplicate Assign for running task {task}")
            await self._adispatch(
                messages.Log(
                    task=task,
                    message="Duplicate assign ignored: the task is already running.",
                    level="DEBUG",
                )
            )
            return True
        if self._has_retained_terminal_report(task):
            logger.warning(f"Duplicate Assign for finished task {task}; re-sending its report")
            for retained in list(self._unacked_events.values()):
                if getattr(retained, "task", None) == task:
                    await self.transport.asend(retained)
            return True
        if task in self._finished_tasks:
            logger.warning(f"Ignoring duplicate Assign for finished task {task}")
            return True
        return False

    async def _aforward_to_running_actor(
        self,
        message: "messages.Cancel | messages.Interrupt | messages.Pause | messages.Resume",
    ) -> None:
        """Forward a lifecycle control message to the actor running that task."""
        if message.task not in self.managed_assignments:
            logger.warning(
                "Received unassignation for a provision that is not running. "
                f"Received: {message.task}"
            )
            await self._adispatch(
                messages.Critical(
                    task=message.task,
                    error="Actors is no longer running and not managed. Probablry there was a restart",
                )
            )
            return

        assignment = self.managed_assignments[message.task]
        actor = self.managed_actors[assignment.interface]
        await actor.apass(message)

    # ----------------------------------------------------------------- caller

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

    async def atear_down(self) -> None:
        """Tears down the agent. This is used to tear down the agent
        and all the actors that are spawned from it.
        """
        logger.info("Tearing down the agent")

        # A failed aconnect leaves the consumer running; aloop stops it itself.
        if self._consume_task is not None:
            await self._astop_consume_task(self._consume_task)
            self._consume_task = None
        if self._control_plane is not None:
            self._control_plane.fail_pending(AgentException("The agent was torn down"))

        for background_task in list(self._background_tasks.values()):
            background_task.cancel()

        for background_task in list(self._background_tasks.values()):
            try:
                await background_task
            except asyncio.CancelledError:
                pass

        # Stop in-flight assignments before the shutdown hooks run, so hooks tear down
        # resources with no work still using them. Bounded, because an actor that
        # swallows cancellation must not be able to hang teardown.
        await self._astop_disconnect_watchdog()
        await self._acancel_actors()

        # Runs while the patch processor, the queue and the transport are still
        # alive, so a hook that touches state still gets its patches flushed by the
        # bounded join below.
        await self.arun_shutdown_hooks()

        if self._event_queue is not None:
            # Best-effort flush of queued patches before stopping the processor.
            # Bounded by a timeout so teardown can never hang on a patch that was
            # enqueued but will not be consumed (e.g. published during shutdown).
            try:
                await asyncio.wait_for(
                    self._event_queue.async_q.join(), timeout=self.teardown_join_timeout
                )
            except (TimeoutError, RuntimeError):
                logger.warning(
                    "Timed out flushing queued patches during teardown; "
                    "closing the patch queue anyway"
                )

        if self._patch_processor_task is not None:
            self._patch_processor_task.cancel()
            try:
                await self._patch_processor_task
            except (asyncio.CancelledError, RuntimeError):
                pass

        if self._event_queue is not None:
            try:
                await self._event_queue.aclose()
            except RuntimeError:
                pass
            self._event_queue = None

        await self.astop_background()
        await self.transport.adisconnect()

        # Reset the connected signal and stored receiver so a subsequent run of a
        # re-entered agent waits for a fresh Init instead of observing stale state.
        self._connected_event.clear()
        self._receiver = None
        self._provider_ready = True
        self._held_provider_messages.clear()

    async def _acancel_actors(self) -> None:
        """Cancel every managed actor's in-flight work, reporting each cancellation.

        Nothing used to do this: teardown looped over a ``managed_actor_tasks`` dict that
        was never populated, so assignments were simply abandoned mid-flight and the
        backend was never told. ``Actor.acancel`` already did the right thing; it just had
        no caller.
        """
        for interface, actor in list(self.managed_actors.items()):
            try:
                await asyncio.wait_for(
                    actor.acancel(), timeout=self.actor_cancel_timeout
                )
            except TimeoutError:
                logger.warning(
                    "Actor %s did not finish cancelling within %.2fs; abandoning it",
                    interface,
                    self.actor_cancel_timeout,
                )
            except Exception:
                logger.error(
                    "Actor %s failed while being cancelled", interface, exc_info=True
                )

    async def aon_connection_change(self, healthy: bool) -> None:
        """React to the transport's link coming up or going down.

        This is the signal the agent never used to get. The transport retries a
        dropped socket transparently, so ``areceive()`` does not end and nothing
        above ever learned that control had been lost — an action that is only safe
        while it can be cancelled kept running regardless.

        Note this does *not* set ``_connected_event`` on link-up: that event means
        "the backend has acknowledged us", which only an ``Init`` can establish.
        """
        if healthy:
            await self._astop_disconnect_watchdog()
        else:
            self._connected_event.clear()
            self._start_disconnect_watchdog()

    def _start_disconnect_watchdog(self) -> None:
        """Begin counting down the grace periods of disconnect-sensitive actors."""
        if (
            self._disconnect_watchdog_task is not None
            and not self._disconnect_watchdog_task.done()
        ):
            return
        self._disconnect_watchdog_task = asyncio.create_task(
            self._adisconnect_watchdog()
        )

    async def _astop_disconnect_watchdog(self) -> None:
        """Stand the watchdog down, because the link is back (or we are shutting down)."""
        task = self._disconnect_watchdog_task
        self._disconnect_watchdog_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _adisconnect_watchdog(self) -> None:
        """Cancel each disconnect-sensitive actor once its grace period expires.

        Only actors whose action declared ``on_disconnect=CANCEL`` are touched;
        everything else keeps running, which is what makes a long acquisition safe
        to leave alone while a stage motor is not.
        """
        down_at = time.monotonic()
        pending = sorted(
            (
                actor
                for actor in list(self.managed_actors.values())
                if actor.policy.cancels_on_disconnect and actor.has_running_tasks()
            ),
            key=lambda actor: actor.policy.grace,
        )
        if not pending:
            return

        for actor in pending:
            remaining = actor.policy.grace - (time.monotonic() - down_at)
            if remaining > 0:
                await asyncio.sleep(remaining)

            # Deadline expiry and link-up can interleave, so a reconnect that lands
            # just after the sleep would otherwise kill work that could have carried
            # on — or worse, kill it while the link is already healthy again.
            if self.transport.connected:
                logger.info(
                    "Link recovered before the grace period elapsed; not cancelling"
                )
                return
            if not actor.has_running_tasks():
                continue

            down_for = time.monotonic() - down_at
            reason = (
                f"cancelled by agent policy: control channel lost for {down_for:.1f}s"
            )
            try:
                await asyncio.wait_for(
                    actor.acancel_for_policy(reason),
                    timeout=self.actor_cancel_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "Actor %s did not stop within %.2fs of the disconnect policy firing",
                    actor.id,
                    self.actor_cancel_timeout,
                )
            except Exception:
                logger.error(
                    "Actor %s failed while being stopped by policy",
                    actor.id,
                    exc_info=True,
                )

    async def aget_hash(self) -> str:
        """A stable hash of this agent's definition, used to skip re-registration.

        The backend does not compute this: it stores whatever hash ``Register`` carries
        and hands it back on ``Init``, skipping the reconciliation when the two match. So
        this only has to be *stable across runs of the same definition* — which is exactly
        what makes that comparison meaningful. Keys are sorted so that
        dictionary ordering cannot change the digest.
        """
        agent_input = self.app_registry.to_implement_agent_input(name=self.name)
        canonical = json.dumps(
            json.loads(agent_input.model_dump_json(by_alias=True, exclude_none=True)),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def _adispatch(self, message: messages.FromAgentMessage) -> None:
        """Assign a stream seq to events, retain terminal reports for ack, then send.

        Every agent→backend event flows through here so it gets a monotonic ``seq``
        and so terminal reports (completed/failed/critical/cancelled/interrupted) are
        retained in ``_unacked_events`` until the backend confirms durability with an
        ``EventAck`` (handled in ``process``) — the persist-then-ack contract.
        """
        if isinstance(message, messages.FromAgentEvent):
            self._event_seq += 1
            # Messages are frozen, so produce a copy carrying the assigned seq.
            message = message.model_copy(update={"seq": self._event_seq})
            if isinstance(message, messages.TERMINAL_REPORTS):
                self._unacked_events[message.id] = message
        await self.transport.asend(message)
        if isinstance(message, messages.TERMINAL_REPORTS):
            # The task is done, so it is no longer running on any actor, and no
            # longer an assignment this agent is managing. Nothing used to pop
            # ``managed_assignments``: it grew without bound, and kept answering
            # backend liveness inquiries about work that had already finished.
            #
            # Pruned only *after* the send: transports may route the message by
            # looking the assignment up (the FastAPI transport resolves the
            # websocket action key from ``managed_assignments``), and pruning
            # first made every terminal event unroutable, so websocket clients saw
            # PROGRESS/YIELD but never COMPLETED or ERROR.
            self.running_assignments.pop(message.task, None)
            self.managed_assignments.pop(message.task, None)
            self._finished_tasks[message.task] = None
            while len(self._finished_tasks) > _FINISHED_TASKS_REMEMBERED:
                self._finished_tasks.pop(next(iter(self._finished_tasks)))

    async def asend(self, actor: "Actor", message: messages.FromAgentMessage) -> None:
        """Sends a message to the actor. This is used for sending messages to the
        agent from the actor. The agent will then send the message to the transport.
        """
        logger.debug(
            f"Agent forwarding {message.id} from actor {actor.__class__.__name__}"
        )
        await self._adispatch(message)

    async def ashrink_state(self, interface: str, state: AnyState) -> Any:  # noqa: ANN401
        """Shrink a state value to its registered schema. Called as the agent starts."""
        if interface not in self._interface_stateschema_input_map:
            raise AgentException(f"State {interface} not found in agent {self.name}")

        schema = self._interface_stateschema_input_map[interface]
        structure_registry = self.get_structure_registry_for_interface(interface)

        # Shrink the value to the schema
        shrinked_state = await ashrink_state(
            state,
            schema,
            structure_reg=structure_registry,
            shelver=self,
        )
        return shrinked_state

    async def ainit_states(self, hook_return: StartupHookReturns) -> None:
        """Initialize the state of the agent. This will be called when the agent starts"""

        state_schemas = self._collected_state_schemas
        missing_initializers = sorted(
            interface
            for interface in state_schemas
            if interface not in hook_return.states
        )
        if missing_initializers:
            missing_states = ", ".join(missing_initializers)
            raise AgentException(
                "Registered states are missing initialization values from startup hooks: "
                f"{missing_states}"
            )

        for interface, startup_value in hook_return.states.items():
            # A startup hook returns a plain instance; it becomes evented here,
            # with this app's rules for the state, and its patches come to this
            # agent from now on. A change made outside a task (a hook) holds no
            # locks.
            declaration = self.app_registry.structure_registry.state_for(
                self.app_registry.state_interface_classes[interface]
            )
            startup_value = evented(startup_value, declaration)
            self.states[interface] = startup_value
            adopt(startup_value, self)

            # Set the state schema that is needed to shrink the state
            self._interface_stateschema_input_map[interface] = state_schemas[interface]

            initial_shrunk_state = await self.ashrink_state(
                interface=interface,
                state=startup_value,
            )

            self._current_shrunk_states[interface] = copy.deepcopy(initial_shrunk_state)

        # TODO: Implement state initialization through dataclass

        # The first thing the backend hears about this run is SessionInit: it opens the
        # session row and records these snapshots as its baseline. Sending a StateSnapshot
        # here instead (as this used to) left the session uninitialised — the backend
        # accepted the snapshot but never learned a session had started.
        session_init = messages.SessionInit(
            session_id=self.current_session,
            states={
                interface: copy.deepcopy(shrunk_state)
                for interface, shrunk_state in self._current_shrunk_states.items()
            },
        )
        logger.debug("Publishing session init: %s ", session_init)
        await self.apublish_session_init(session_init)

    async def apublish_session_init(self, session_init: messages.SessionInit) -> None:
        """Announce a new session, with the state snapshots it starts from."""
        await self.transport.asend(session_init)
        logger.debug("Published session init %s", session_init)

    async def apublish_patch(self, patch: messages.StatePatch) -> None:
        """Publish a state patch over the socket."""
        await self.transport.asend(patch)
        logger.debug("Published patch %s", patch)

    async def apublish_snapshot(self, snapshot: messages.StateSnapshot) -> None:
        """Publish a full state snapshot over the socket."""
        await self.transport.asend(snapshot)
        logger.debug("Published snapshot %s", snapshot)

    async def aget_context(self, context: str) -> Any:  # noqa: ANN401
        """Get a context from the agent. This is used to get contexts from the
        agent from the actor."""
        if context not in self.contexts:
            raise AgentException(f"Context {context} not found in agent {self.name}")
        return self.contexts[context]

    async def aget_bound_app(self) -> BoundApp | None:
        """Get the app this agent is bound to, or ``None`` when it runs on its own."""
        return self.bound_app

    def _bound_app_kwargs(self, hook: Any) -> dict[str, Any]:  # noqa: ANN401
        """``bound_app=`` for hooks that accept it; nothing for hook classes written
        against the bare protocols, which are called exactly as before."""
        if getattr(hook, "takes_bound_app", False):
            return {"bound_app": self.bound_app}
        return {}

    async def arun_background(self) -> None:
        """Run the background tasks. This will be called when the agent starts."""

        for name, worker in self._collected_background_workers.items():
            task = asyncio.create_task(
                worker.arun(
                    self,
                    contexts=self.contexts,
                    states=self.states,
                    app_context=self._app_context,
                    **self._bound_app_kwargs(worker),
                ),
            )
            task.add_done_callback(
                lambda task, name=name: self._on_background_done(name, task)
            )
            self._background_tasks[name] = task

    def _on_background_done(self, name: str, task: "asyncio.Task[None]") -> None:
        """Done-callback for background worker tasks. Removes the task from the
        registry and logs any genuine failure (ignoring cancellation)."""
        self._background_tasks.pop(name, None)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.error("Worker %s failed with exception: %s", name, exception)

    async def astop_background(self) -> None:
        """Stop the background tasks. This will be called when the agent stops."""
        for _, task in self._background_tasks.items():
            task.cancel()

        try:
            await asyncio.gather(
                *self._background_tasks.values(), return_exceptions=True
            )
        except asyncio.CancelledError:
            pass

    async def arun_startup_hooks(
        self, app_context: AppContext | None = None
    ) -> StartupHookReturns:
        """Run all startup hooks collected from the app registry.

        Returns:
            StartupHookReturns: The combined states and contexts from all hooks.
        """
        from rekuest.agents.hooks.errors import StartupHookError

        states: dict[str, Any] = {}
        contexts: dict[str, Any] = {}

        for key, hook in self._collected_startup_hooks.items():
            try:
                answer = await asyncio.wait_for(
                    hook.arun(
                        app_context=app_context, **self._bound_app_kwargs(hook)
                    ),
                    timeout=20,
                )
                for i in answer.states:
                    if i in states:
                        raise StartupHookError(f"State {i} already defined")
                    states[i] = answer.states[i]

                for i in answer.contexts:
                    if i in contexts:
                        raise StartupHookError(f"Context {i} already defined")
                    contexts[i] = answer.contexts[i]

            except Exception as e:
                raise StartupHookError(f"Startup hook {key} failed") from e

        return StartupHookReturns(states=states, contexts=contexts)

    async def arun_shutdown_hooks(self) -> None:
        """Run all shutdown hooks collected from the app registry.

        Runs in the reverse of the registration order, so teardown unwinds what
        startup set up. Only runs for an agent that got as far as its startup
        hooks, and only once per start. A hook that fails or times out is logged
        and the remaining hooks still run: teardown must never fail because of a
        shutdown hook.
        """
        from rekuest.agents.hooks.errors import ShutdownHookError

        if not self._ran_startup_hooks:
            return
        self._ran_startup_hooks = False

        for key, hook in reversed(list(self._collected_shutdown_hooks.items())):
            try:
                await asyncio.wait_for(
                    hook.arun(
                        agent=self,
                        contexts=self.contexts,
                        states=self.states,
                        app_context=self._app_context,
                        **self._bound_app_kwargs(hook),
                    ),
                    timeout=self.shutdown_hook_timeout,
                )
            except Exception as e:
                hook_error = ShutdownHookError(f"Shutdown hook {key} failed")
                hook_error.__cause__ = e
                logger.error(hook_error, exc_info=hook_error)

    @property
    def registered_agent_id(self) -> str | None:
        """The id this agent's backend assigned it, once registered.

        ``None`` before the backend has acknowledged the agent, and for a backend that
        assigns none.
        """
        return self.backend.registered_agent_id

    @property
    def app_context(self) -> AppContext | None:
        """What the run handed this agent as its app context (``run(context=...)``)."""
        return self._app_context

    async def astart(self, app_context: AppContext | None = None) -> None:
        """What happens before the socket opens: read the registry, mint the session.

        The rest of starting -- hooks, states, background work -- is :meth:`aactivate`,
        which runs once the backend has acknowledged the agent, because it may need the
        socket (a startup state holding a memory structure is shelved over it).

        Raises:
            AppContextError: If ``app_context`` is not what the registry declared
                (missing, of another class, or given to an app that declares none).
        """
        # First: a wrong context must never reach a hook or an action.
        self.app_registry.require_app_context(app_context, whose="This app")
        # Remembered here (not only in aconnect) so background workers started by
        # this method can be handed the app context they were declared against.
        self._app_context = app_context
        # Collect state schemas, startup hooks and background workers from the app registry
        self.collect_from_registry()
        self.current_session = await self._acreate_session()

    async def aactivate(self, app_context: AppContext | None = None) -> None:
        """Run the startup hooks, initialize the states and start the background work.

        Runs after ``Init``: the agent is registered and its socket answers.
        """
        # A state is built unrestricted by its startup hook; adopting it (in
        # ainit_states) is what makes later changes need their locks.
        hook_return = await self.arun_startup_hooks(app_context=app_context)
        # From here on the app has set up resources, so teardown owes it the
        # shutdown hooks (even if the rest of the startup fails).
        self._ran_startup_hooks = True
        await self.ainit_states(hook_return=hook_return)

        self.global_revision = 0
        self._event_queue = janus.Queue()
        self._patch_processor_task = asyncio.create_task(
            self.apatch_event_loop()
        )
        self._patch_processor_task.add_done_callback(
            lambda x: (
                logger.error(f"Patch processor task failed: {x.exception()}")
                if not x.cancelled() and x.exception() is not None
                else None
            )
        )

        for context_key, context_value in hook_return.contexts.items():
            self.contexts[context_key] = context_value

        await self.arun_background()

    async def aspawn_actor_from_assign(self, assign: messages.Assign) -> Actor:
        """Spawns an Actor from a Assign.

        Actors are spawned on assign rather than up front, because some are meta actors
        that are not registered ahead of time but created on demand from the assign.
        """

        try:
            actor_builder = self.app_registry.get_builder_for_interface(
                assign.interface
            )
        except KeyError:
            raise ProvisionException(
                f"No actor builder found for interface {assign.interface} in agent {self.name}"
            )

        actor = actor_builder(agent=self)

        self.managed_actors[assign.interface] = actor
        self.managed_assignments[assign.task] = assign

        return actor

    async def _aawait_on_loop(self, awaitable: Awaitable[T]) -> T:
        """Await something the running message loop will resolve; fail if the loop ends first.

        The consumer task started by ``aconnect`` delivers ``Init`` and every reply the
        activation waits on. If it ends before ``awaitable`` completes -- the stream
        closed, or it raised (kicked, a protocol error) -- that is what the caller learns,
        so ``aconnect`` never reports a connection that does not exist.
        """
        consume_task = self._consume_task
        assert consume_task is not None, "the consumer task must run first"
        waiter = asyncio.ensure_future(awaitable)
        try:
            done, _ = await asyncio.wait(
                {waiter, consume_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            waiter.cancel()
            raise
        if waiter in done:
            return waiter.result()
        waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await waiter
        if not consume_task.cancelled() and consume_task.exception() is not None:
            raise consume_task.exception()  # type: ignore[misc]
        raise AgentException(
            "The transport closed before the server acknowledged the agent"
        )

    async def _await_acknowledged(self) -> None:
        """Wait until the server has acknowledged the agent (an ``Init`` arrived).

        Subclasses whose transport has no acknowledgement handshake (e.g. the
        server-side FastAPI transport) override this.
        """
        await self._aawait_on_loop(self._connected_event.wait())

    async def aget_handshake_params(self) -> HandshakeParams:
        """Supply the agent-owned half of registering a connection.

        Called by the transport once per connect attempt, which is what lets a reconnect
        carry the *current* session id rather than one captured at build time. The
        declaration goes with it: registering is implementing, so ``Register`` carries
        what this agent offers and its definition hash (the backend skips the
        reconciliation when it already holds that hash).
        """
        agent_input = self.app_registry.to_implement_agent_input(name=self.name)
        # ``by_alias=False``: the aliases on the generated input models are the
        # *GraphQL* wire spelling (``portGroups``), and this is not GraphQL. The
        # socket's own models -- the backend's ``rekuest_core.inputs.models`` --
        # are spelled in snake_case and forbid extras, so an aliased dump is
        # refused field by field at registration.
        declaration = messages.AgentDeclaration(
            hash=await self.aget_hash(),
            **agent_input.model_dump(mode="json", by_alias=False, exclude_unset=True),
        )
        return HandshakeParams(
            force=self.force,
            session_id=self.current_session,
            declaration=declaration,
        )

    async def _aconnect_sequence(self, context: AppContext | None = None) -> None:
        """The startup + transport-open + acknowledge + activate sequence, unbounded.

        Each phase is logged so that a stall (when ``aconnect`` wraps this in a
        timeout) points to the exact phase that hung. The message consumer starts as
        soon as the transport is open: ``Init`` and the replies activation waits on
        (a shelved startup state) are delivered by it.
        """
        logger.debug("aconnect: running astart")
        await self.astart(app_context=context)
        logger.debug("aconnect: opening transport")
        self._provider_ready = False
        # Installed before the socket opens so the very first Register carries the
        # session id and the declaration, not just the ones after a reconnect, and so
        # a drop during the very first connection already reaches the disconnect watchdog.
        self.transport.set_transport_host(self)
        self._receiver = self.transport.areceive().__aiter__()
        await self.transport.aconnect()
        self._consume_task = asyncio.create_task(self._aconsume_messages())
        logger.debug("aconnect: awaiting acknowledgement")
        await self._await_acknowledged()
        logger.debug("aconnect: activating")
        await self._aawait_on_loop(self.aactivate(app_context=context))
        await self._arelease_held_provider_messages()
        logger.info("Agent connected, registered and started")

    async def aconnect(
        self,
        context: AppContext | None = None,
        timeout: float | None = None,
    ) -> None:
        """Starts the agent and connects to the transport.

        This runs the startup phase, opens the transport (the ``Register`` carries the
        agent's declaration), waits for the server to acknowledge and thereby register
        the agent (an ``Init`` message), and activates it. The message consumer is left
        running so that ``aloop`` can adopt it.

        The whole sequence (including ``astart``) is bounded by ``timeout``: if it
        does not complete within that many seconds, ``asyncio.TimeoutError`` is
        raised and the agent is torn down.
        """
        self._app_context = context

        try:
            sequence = self._aconnect_sequence(context=context)
            if timeout is not None:
                await asyncio.wait_for(sequence, timeout)
            else:
                await sequence
        except BaseException:
            logger.error("Agent failed to connect", exc_info=True)
            await self.atear_down()
            raise

    async def _aconsume_messages(self) -> None:
        """Resume the transport stream and process every message it yields."""
        assert self._receiver is not None, "aconnect() must run before aloop()"
        async for message in self._receiver:
            await self.process(message)

    async def aloop(self) -> None:
        """Async loop that processes messages after the agent has connected.

        The transport stream is consumed in a dedicated child task. This keeps
        cancellation responsive: some streams (e.g. a websocket mid-close) do not
        promptly honour cancellation, which would otherwise leave ``aloop`` stuck
        in the "cancelling" state forever and never run teardown. On cancellation
        we cancel the consumer, wait for it only up to ``cancel_grace_period``,
        and then always tear down.
        """
        self.running = True
        consume_task = self._consume_task
        if consume_task is None:
            raise AgentException("aconnect() must run before aloop()")
        try:
            # Shield so that cancelling ``aloop`` returns control here immediately
            # instead of blocking on ``consume_task`` (which may be stuck unwinding
            # a stream that ignores cancellation). We then stop it with a bound.
            await asyncio.shield(consume_task)
        except asyncio.CancelledError:
            logger.info(f"Provisioning task cancelled. We are running {self.transport}")
            self.running = False
            await self._astop_consume_task(consume_task)
            await self.atear_down()
            raise
        except Exception as e:
            logger.error(f"Error in agent loop: {str(e)}")
            await self._astop_consume_task(consume_task)
            await self.atear_down()
            raise e

    async def _astop_consume_task(self, consume_task: "asyncio.Task[None]") -> None:
        """Stop the message-consumer task, bounded by ``cancel_grace_period``.

        The transport is deliberately left connected: teardown still publishes
        (shutdown hooks, final state patches), and those messages need a live
        socket. ``atear_down`` disconnects at the end, once everything is out.

        Only if the consumer refuses to unwind do we disconnect early to release a
        stream that ignores cancellation, so teardown can never hang on it.
        """
        if consume_task.done():
            return
        consume_task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(consume_task), timeout=self.cancel_grace_period
            )
            return
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception:
            logger.warning("Message consumer errored during shutdown", exc_info=True)
            return

        if consume_task.done():
            return

        logger.warning(
            "Message consumer did not unwind in time; disconnecting the transport to "
            "release it. Messages queued during teardown may be lost."
        )
        try:
            await self.transport.adisconnect()
        except Exception:
            logger.warning("Transport disconnect during shutdown failed", exc_info=True)
        try:
            await asyncio.wait_for(
                asyncio.shield(consume_task), timeout=self.cancel_grace_period
            )
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception:
            logger.warning("Message consumer errored during shutdown", exc_info=True)

    async def aprovide(self, context: AppContext | None = None) -> None:
        """Provides the agent.

        This starts the agent, connects to the transport, and then listens for
        messages from the transport. It is simply ``aconnect`` followed by
        ``aloop``.
        """
        try:
            logger.info("Launching provisioning task.")
            await self.aconnect(context=context)
            await self.aloop()
        except asyncio.CancelledError:
            logger.info("Provisioning task cancelled. We are running")
            await self.atear_down()
            raise

    async def __aenter__(self) -> Self:
        """Enter the agent context manager. This is used to enter the agent

        context manager and start the agent. The agent will then start the
        transport and start listening for messages from the transport.
        """

        await self.transport.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the agent.

        This method is called when the agent is exited. It is responsible for
        tearing down the agent and all the actors that are spawned from it.

        Args:
            exc_type (Optional[type]): The type of the exception
            exc_val (Optional[Exception]): The exception value
            exc_tb (Optional[type]): The traceback

        """
        await self.atear_down()
        await self.transport.__aexit__(exc_type, exc_val, exc_tb)


class RekuestAgent(BaseAgent):
    """The Rekuest Agent

    The default agent: a :class:`BaseAgent` whose backend is a Rekuest server, reached
    over the agent's own socket. It registers by connecting (``Register`` carries its
    declaration) and shelves through :class:`~rekuest.agents.backend.SocketAgentBackend`;
    this class only has to pick that backend.
    """

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        """Shelve over the socket unless the caller passed a backend explicitly."""
        super().model_post_init(__context)
        if isinstance(self.backend, LocalAgentBackend):
            self.backend = SocketAgentBackend(control_plane=self.control_plane)
