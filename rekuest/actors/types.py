"""Types for the actors module"""

import asyncio
from typing import (
    TYPE_CHECKING,
    Protocol,
    Self,
    runtime_checkable,
    Any,
    Literal,
)
from collections.abc import Awaitable
from rekuest.agents.types import BoundApp
from rekuest import messages
from rekuest.actors.policy import KEEP, DisconnectPolicy
from rekuest.agents.context import PreparedContextReturns, PreparedContextVariables
from rekuest.coercible_types import OptimisticCoercible
from rekuest.postmans.types import Postman
from rekuest.protocols import AnyFunction, AnyState
from rekuest.scalars import Identifier
from rekuest.state.publish import Patch
from rekuest.structures.registry import StructureRegistry
from rekuest.api.schema import (
    PortGroupInput,
    TestTargetInput,
    TrackInput,
    ValidatorInput,
)
from rekuest.definition.define import (
    AssignWidgetMap,
    DefinitionInput,
    EffectsMap,
    ReturnWidgetMap,
)
from collections.abc import Sequence, Callable
from dataclasses import dataclass, field


if TYPE_CHECKING:
    from rekuest.app import AppRegistry
    from rekuest.agents.lock import TaskLock


@dataclass
class AssignmentHook:
    """A hook that is called when an assignment is received. This can be used to
    modify the assignment before it is processed by the actor.
    """

    id: str
    kind: str
    hook: Callable[[messages.ToAgentMessage], Awaitable[None]]


@dataclass
class PreparedStateVariables:
    write_state_variables: dict[str, str]
    read_only_variables: dict[str, str]
    required_state_locks: dict[str, list[str]]

    @property
    def count(self) -> int:
        """Get the amount of state variables."""
        return len(self.write_state_variables) + len(self.read_only_variables)

    @property
    def variable_keys(self) -> list[str]:
        """Get the keys of the state variables."""
        return list(self.write_state_variables.keys()) + list(
            self.read_only_variables.keys()
        )


@dataclass
class PreparedAppContextVariables:
    app_context_variables: dict[str, type[Any]]
    """The app-context class each parameter asks for, by parameter name."""

    @property
    def count(self) -> int:
        """Get the amount of state variables."""
        return len(self.app_context_variables)


@dataclass
class PreparedInjectedVariables:
    """The parameters of a function that are injected rather than ports.

    ``service_client_variables`` map a parameter to the client class it receives
    from the app the agent is bound to (``mikro: Mikro``); ``task_variables``
    receive the :class:`rekuest.task.Task` being run; ``app_context_variables``
    receive the app context the run started the agent with, keyed by the class
    the app declared.
    """

    service_client_variables: dict[str, type] = field(default_factory=dict)
    task_variables: list[str] = field(default_factory=list)
    app_context_variables: dict[str, type] = field(default_factory=dict)

    @property
    def count(self) -> int:
        """Get the amount of injected variables."""
        return (
            len(self.service_client_variables)
            + len(self.task_variables)
            + len(self.app_context_variables)
        )


@dataclass
class PreparedDependencyVariables:
    dependency_variables: dict[str, Any]


@dataclass
class PreparedStateReturns:
    state_returns: dict[int, str]

    @property
    def count(self) -> int:
        """Get the amount of state returns."""
        return len(self.state_returns)


@dataclass
class PreparedAppContextReturns:
    app_context_returns: dict[int, type[Any]]
    """The app-context class each return position publishes, by index."""

    @property
    def count(self) -> int:
        """Get the amount of app context returns."""
        return len(self.app_context_returns)


@dataclass
class ImplementationDetails:
    state_variables: PreparedStateVariables
    state_returns: PreparedStateReturns
    context_variables: PreparedContextVariables
    context_returns: PreparedContextReturns
    dependency_variables: PreparedDependencyVariables
    locks: list[str] | None = None
    tracks: list["TrackInput"] | None = None
    manipulates: list[str] | None = None
    injected_variables: PreparedInjectedVariables = field(
        default_factory=PreparedInjectedVariables
    )

    def actor_kwargs(self) -> dict[str, Any]:
        """Everything derived from the function that its actor is built with.

        Every actifier passes this whole, so a new kind of injected parameter
        reaches every actor: hand-listing the fields is how the Qt builder and
        fluss's flow actifier each silently dropped the injected variables.
        """
        return {
            "state_variables": self.state_variables,
            "state_returns": self.state_returns,
            "context_variables": self.context_variables,
            "context_returns": self.context_returns,
            "dependency_variables": self.dependency_variables,
            "injected_variables": self.injected_variables,
            "locks": self.locks,
        }


@runtime_checkable
class Shelver(Protocol):
    """A protocol for mostly fullfield by the agent that is used to store data"""

    async def aput_on_shelve(
        self,
        identifier: Identifier,
        value: Any,  # noqa: ANN401
    ) -> str:  # noqa: ANN401
        """Put a value on the shelve and return the key. This is used to store
        values on the shelve."""
        ...

    async def aget_from_shelve(self, key: str) -> Any:  # noqa: ANN401
        """Get a value from the shelve. This is used to get values from the
        shelve."""
        ...


@runtime_checkable
class Capturable(Protocol):
    """The capture gate an actor's debug tooling coordinates on.

    Only :mod:`rekuest.actors.debug` touches this, which is why it is its own slice
    rather than part of what every actor sees.
    """

    capture_condition: asyncio.Condition
    capture_active: bool


@runtime_checkable
class LockHost(Protocol):
    """Reports lock acquisition, and resolves an actor's declared lock keys.

    Implemented by the agent and used by :class:`~rekuest.agents.lock.TaskLock`.
    """

    async def alock(self, key: str, task: str) -> None:
        """Report that a task has acquired a lock."""
        ...

    async def aunlock(self, key: str) -> None:
        """Report that a task has released a lock."""
        ...

    def get_locks_for_keys(self, keys: Sequence[str]) -> list["TaskLock"]:
        """Resolve the agent's task locks for the given lock keys."""
        ...


@runtime_checkable
class ActorContext(Shelver, LockHost, Capturable, Protocol):
    """Everything a running actor needs from the agent above it — and nothing more.

    Deliberately excludes the agent's own lifecycle (``aprovide`` / ``aconnect`` /
    ``aloop``): no actor calls those, and having them in one flat protocol made it read as
    though an actor could drive the agent it runs inside.
    """

    app_registry: "AppRegistry"

    @property
    def caller_postman(self) -> Postman:
        """The agent-as-caller postman: a per-task ``Rekuest`` view calls through it.

        Declared as a property, not an attribute: implementations build it lazily, and a
        mutable protocol attribute is invariant, so a read-only property would not satisfy it.
        """
        ...

    async def asend(self, actor: "Actor", message: messages.FromAgentMessage) -> None:
        """Send a message from an actor up to the agent, which forwards it onward."""
        ...

    async def aget_read_only_proxy(self, key: str) -> AnyState:  # noqa: ANN401
        """Get a state an actor only reads. See the note on the agent implementation:
        read-only is declarative, not enforced."""
        ...

    async def aget_write_proxy(self, key: str, mutation: Any = None) -> AnyState:  # noqa: ANN401
        """Get a state an actor writes to, as a view for ``mutation`` (a task's)."""
        ...

    async def aget_context(self, context: str) -> Any:  # noqa: ANN401
        """Get a context value registered with ``@context``."""
        ...

    async def aget_bound_app(self) -> BoundApp | None:
        """Get the app this agent is bound to, or ``None`` when it runs on its own.

        It answers ``get(cls)`` with the app's clients, which an actor injects
        into parameters annotated with a client class.
        """
        ...

    def publish_patch(self, interface: str, patch: Patch) -> None:
        """Publish a state patch. Satisfies ``state.publish.StateHolder``."""
        ...


@runtime_checkable
class AgentLifecycle(Protocol):
    """Driving the agent itself — what the composition root uses, not what actors use.

    Called only from :class:`~rekuest.client.client.Rekuest` and the FastAPI routes.
    """

    force: bool | None
    """Kick any connection already registered for this agent and take over. ``None``
    defers to the transport's own build-time policy. Settable per run."""

    async def aprovide(self, context: Any) -> None:  # noqa: ANN401
        """Connect, then process messages until cancelled."""
        ...

    async def aconnect(self, context: Any = None, timeout: float | None = None) -> None:
        """Start the agent and connect, returning once the server acknowledges it."""
        ...

    async def aloop(self) -> None:
        """Process incoming messages after the agent has connected."""
        ...


@runtime_checkable
class Agent(ActorContext, AgentLifecycle, Protocol):
    """The whole agent surface: what actors need plus what drives it.

    Kept as the union of the slices above so every existing annotation and import keeps
    working. Prefer the narrowest slice that fits when writing new code —
    :class:`ActorContext` for anything an actor reaches, :class:`AgentLifecycle` for
    anything that starts or stops the agent.
    """


@runtime_checkable
class Actor(Protocol):
    """An actor is a function that takes a passport and a transport"""

    id: str
    """Stable identifier for this actor, recorded against the tasks it is running."""
    agent: Agent
    policy: DisconnectPolicy
    """What happens to this actor's work when the agent loses its control channel."""

    def has_running_tasks(self) -> bool:
        """Whether this actor currently has any assignment in flight."""
        ...

    def install_assignment_hook(self, task_id: str, hook: AssignmentHook) -> None:
        """Install an assignment hook for the current task.

        Args:
            task_id (str): The task to install the hook for.
            hook (AssignmentHook): The hook to install.
        """
        ...

    async def acancel(self) -> None:
        """Stop every assignment this actor is running.

        Called by the agent as it tears down, so in-flight work does not outlive the
        agent that owns it. Each cancelled task is reported to the backend.
        """
        ...

    async def acancel_for_policy(self, reason: str) -> int:
        """Stop every assignment this actor is running, per the disconnect policy.

        Distinct from :meth:`acancel`: it reports the given reason rather than the
        teardown wording, and prunes its task bookkeeping so a later liveness
        inquiry does not claim the killed work is still running. Returns how many
        assignments were stopped.
        """
        ...

    async def abreak(self, task_id: str) -> bool:
        """Break the actor. This method will break the actor and return None.
        This is used to break the actor"""
        ...

    async def asend(
        self: Self,
        message: messages.FromAgentMessage,
    ) -> None:
        """Send a message to the actor. This method will send a message to the
        actor and return None.
        """
        ...

    async def apass(
        self: Self,
        message: messages.ToAgentMessage,
    ) -> None:
        """Pass a message to the actor. This method will pass a message to the
        actor and return None.
        """
        ...

    async def acheck_task(
        self: Self,
        task_id: str,
    ) -> bool:
        """Check the task. This method will check the task and
        return None.
        """
        ...


@runtime_checkable
class ActorBuilder(Protocol):
    """An actor builder is a function that takes a passport and a transport
    and returns an actor. This method will create the actor and return it.
    """

    def __call__(
        self,
        agent: Agent,
    ) -> Actor:
        """Create the actor and return it. This method will create the actor and"""

        ...


@dataclass
class RegisterConfig:
    """Bundle of every option that shapes a registered function's definition and
    implementation.

    This is the single source of truth for the registration options. The public
    ``register`` decorator builds one of these from its keyword arguments and threads
    it — as a single object — down through ``register_func`` and the actifier, instead
    of re-listing ~20 parameters at every hop.

    The fields fall into two groups:

    * **definition-shaping** — unpacked by the actifier into ``prepare_definition``:
      ``name``, ``description``, ``widgets``, ``return_widgets``, ``effects``,
      ``validators``, ``collections``, ``port_groups``,
      ``is_test_for``, ``stateful``, ``version``, ``key``.
    * **implementation/actor-shaping** — used by the actifier's actor build and by
      ``register_func`` when constructing the ``ImplementationInput``:
      ``optimistics``, ``locks``, ``tracks``, ``manipulates``, ``in_process``,
      ``bypass_shrink``, ``bypass_expand``, ``auto_locks``, ``concurrency``,
      ``policy``.
    """

    # definition-shaping
    name: str | None = None
    description: str | None = None
    interface: str | None = None
    widgets: AssignWidgetMap | None = None
    return_widgets: ReturnWidgetMap | None = None
    effects: EffectsMap | None = None
    validators: dict[str, list[ValidatorInput]] | None = None
    collections: list[str] | None = None
    port_groups: list[PortGroupInput] | None = None
    is_test_for: list[TestTargetInput] | None = None
    stateful: bool = False
    version: str | None = None
    key: str | None = None
    catalogs: list[str] | None = None
    """Names of the UI catalogs that extend the base catalog (``base@1``, always applied) for the definition's effect and validator calls."""
    # implementation / actor-shaping
    optimistics: list[OptimisticCoercible] | None = None
    locks: list[str] | None = None
    tracks: list[TrackInput] | None = None
    manipulates: list[str] | None = None
    in_process: bool = False
    bypass_shrink: bool = False
    bypass_expand: bool = False
    auto_locks: bool = True
    concurrency: Literal["parallel", "serial"] = "serial"
    policy: DisconnectPolicy = KEEP


@runtime_checkable
class Actifier(Protocol):
    """An actifier is a function that takes a callable, a structure registry and a
    bundled :class:`RegisterConfig`, and returns a definition, implementation details
    and an actor builder.
    """

    def __call__(
        self,
        function: AnyFunction,
        structure_registry: StructureRegistry,
        config: RegisterConfig | None = None,
    ) -> tuple[DefinitionInput, ImplementationDetails, ActorBuilder]:
        """A function that will inspect the function and return a definition and
        an actor builder. This method will inspect the function and return a
        definition and an actor builder.
        """
        ...
