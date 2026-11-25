"""The base client for rekuest"""

import os
from collections.abc import AsyncGenerator, Generator
from typing import (
    Annotated,
    Any,
    ClassVar,
    Self,
    TypeVar,
)

from arkitekt_runtime.invoke import AgentTarget
from arkitekt_runtime.postmans.types import Postman
from arkitekt_runtime.types import TaskPolicy
from arkitekt_spec.declare.client import client
from arkitekt_spec.declare.errors import RootOnlyCallError
from arkitekt_spec.declare.protocol.types import AnyFunction
from arkitekt_spec.declare.register import WrappedFunction
from arkitekt_spec.declare.task import HookInput, StateRequirement
from arkitekt_spec.declare.tested import tested_against
from arkitekt_spec.declare.wiring import Alias, Require, TokenLoader
from koil import unkoil, unkoil_gen
from koil.composition import Composition
from pydantic import PrivateAttr
from rath.origin import origin_context
from rath.scalars import ID
from rath.task import current_task
from rath.turms.funcs import TOperation

from rekuest.api.schema import (
    Action,
    AssignMutation,
    Implementation,
    RekuestApi,
    RekuestCallerApi,
)
from rekuest.client.rath import RekuestRath

#: What :meth:`Rekuest.aresolve` accepts: a fetched model, an id, or a function this app
#: registered. Only the client can turn the last two into the first -- that takes GraphQL.
CallTarget = Action | Implementation | WrappedFunction[Any, Any] | ID | str


T = TypeVar("T", bound=AnyFunction)

TESTED = tested_against("rekuest")
"""The rekuest server this release wants: the one its release job tested it against."""

_API = os.path.join(os.path.dirname(os.path.dirname(__file__)), "api")
REKUEST = Require(
    "live.arkitekt.rekuest",
    "Where this app's actions are offered and assigned",
    version=TESTED,
)
S3 = Require("live.arkitekt.s3", "Where this app's uploads are stored")


@client(
    schema=os.path.join(_API, "schema.graphql"),
    turms=os.path.join(_API, "project.json"),
    # The server this release is written against: what a hub made for an app's
    # tests runs. Moves with the server's major, not with this package's.
    image="jhnnsrs/rekuest:23",
)
class Rekuest(Composition, RekuestApi):
    """The rekuest client: every rekuest operation but those that start or steer a task is
    a method of it -- lookups, queries, blok and asset mutations.

    A run builds it; it knows nothing about any agent. An action asks for it by
    annotation (``rekuest: Rekuest``) and is handed this one shared instance. It starts
    no task: an action calls through its declared dependencies, or (``dynamic_calls``)
    through its task, ``await task.acall(await rekuest.aresolve(target), ...)``. An app
    that starts tasks of its own uses :class:`RekuestCaller` instead, whose scope it
    then asks for.
    """

    rath: RekuestRath
    # Private: a Composition enters its fields, and the run is not this client's to enter.
    _origin: Any = PrivateAttr(default=None)
    """The run this client belongs to: what the references its calls return expand through."""

    def attach(self, app: Any, /) -> None:  # noqa: ANN401 -- a BoundApp
        """Belong to ``app``, so the references a call returns expand through its clients."""
        self._origin = app

    @classmethod
    def from_fakts(
        cls,
        rekuest: Annotated[Alias, REKUEST],
        s3: Annotated[Alias, S3],
        tokens: TokenLoader,
    ) -> Self:
        """Built by a run: talks to the resolved rekuest."""
        from rekuest.client.connect import rekuest_rath

        return cls(rath=rekuest_rath(rekuest, s3, tokens))

    def _serialize(
        self, operation: type[TOperation], variables: dict[str, Any]
    ) -> dict[str, Any]:
        return operation.Arguments(**variables).model_dump(
            by_alias=True, exclude_unset=True
        )

    def execute(
        self, operation: type[TOperation], variables: dict[str, Any]
    ) -> TOperation:
        """Executes a query or mutation in a blocking way."""
        return unkoil(self.aexecute, operation, variables)

    async def aexecute(
        self, operation: type[TOperation], variables: dict[str, Any]
    ) -> TOperation:
        """Executes a query or mutation in a non-blocking way."""
        x = await self.rath.aquery(
            operation.Meta.document, self._serialize(operation, variables)
        )
        return operation.model_validate(
            x.data, context=origin_context(client=self, rath=self.rath)
        )

    def subscribe(
        self, operation: type[TOperation], variables: dict[str, Any]
    ) -> Generator[TOperation, None, None]:
        """Subscribes to an operation in a blocking way."""
        return unkoil_gen(self.asubscribe, operation, variables)

    async def asubscribe(
        self, operation: type[TOperation], variables: dict[str, Any]
    ) -> AsyncGenerator[TOperation, None]:
        """Subscribes to an operation in a non-blocking way."""
        async for event in self.rath.asubscribe(
            operation.Meta.document, self._serialize(operation, variables)
        ):
            yield operation.model_validate(
                event.data, context=origin_context(client=self, rath=self.rath)
            )

    async def aresolve(self, target: "CallTarget") -> Action | Implementation:
        """What to call: an action, an implementation, an action id, or a function
        registered on this client's app (its implementation there)."""
        if isinstance(target, (Action, Implementation)):
            return target
        if isinstance(target, WrappedFunction):
            return await self.amy_implementation_at(target.interface)
        if isinstance(target, (ID, str)):
            return await self.afind(target)
        raise ValueError(
            "A call target is an Action, an Implementation, an action id, or a "
            f"function registered on this app; got {type(target).__name__}"
        )


@client(
    schema=os.path.join(_API, "schema.graphql"),
    turms=os.path.join(_API, "project.json"),
    image="jhnnsrs/rekuest:23",
)
class RekuestCaller(Rekuest, RekuestCallerApi):
    """The rekuest client that also starts and steers tasks at the top level: calls
    actions (``rekuest.call(...)``), assigns, cancels, pauses, resumes, interrupts and
    decides.

    An app has it only by using it (``App(clients=[RekuestCaller])``), and so asks for
    ``rekuest_root_call`` at login: an app that does not can start no task of its own,
    only call what its actions declare.

    **Its calls are roots.** A call through this client goes over its own postman with no
    parent, so inside a running task it refuses -- a call made then is that task's child,
    and the task is what knows the socket and the assignment to make it one. Look the
    action up here (:meth:`aresolve`, :meth:`afind`) and call it through the task:
    ``await task.acall(await rekuest.aresolve(target), ...)``. Only the call methods and
    ``assign`` refuse; every lookup, query and other mutation works inside a task as before.
    """

    postman: Postman

    __scopes__: ClassVar[tuple[str, ...]] = tuple(
        sorted({*RekuestApi.__scopes__, *RekuestCallerApi.__scopes__})
    )
    """What its operations need: the plain client's, and starting tasks at the top level."""

    @classmethod
    def from_fakts(
        cls,
        rekuest: Annotated[Alias, REKUEST],
        s3: Annotated[Alias, S3],
        tokens: TokenLoader,
    ) -> Self:
        """Built by a run: the plain client's connection, and a caller session for its calls."""
        from rekuest.client.connect import caller_postman, rekuest_rath

        return cls(
            rath=rekuest_rath(rekuest, s3, tokens),
            postman=caller_postman(rekuest, tokens),
        )

    async def aexecute(
        self, operation: type[TOperation], variables: dict[str, Any]
    ) -> TOperation:
        """Executes a query or mutation in a non-blocking way; an assign refuses inside a task."""
        if operation is AssignMutation:
            self._refuse_in_task("aassign")
        return await super().aexecute(operation, variables)

    def _refuse_in_task(self, method: str) -> None:
        """Refuse a root call while a task is running.

        The predicate is an *agent-run* ambient task, not merely any ambient task: a
        ``Task.local()`` has no agent and no assignment, so a call under one is legitimately
        a parentless root and ``task.acall`` would itself refuse it. The two conditions are
        exact complements -- agent-run task: the client refuses and the task calls; local
        task or none: the client calls and the task refuses.

        The ambient task is *read* here and nowhere else. What used to borrow
        ``agent.caller_postman`` from it is gone; nothing ambient reaches a request.

        Only the call methods use this. A lookup made inside a task -- ``aresolve``,
        ``afind``, ``acollect``, any generated query, the structure expanders -- is correct
        and common, so do not move this into ``aexecute``.
        """
        task = current_task.get()
        if task is None or getattr(task, "agent", None) is None:
            return
        raise RootOnlyCallError(
            f"rekuest.{method}(...) makes a root task, but task "
            f"{getattr(task, 'id', '?')!r} is running, so a call made now is its child. "
            f"Call it through the task -- task.{method}(action) -- resolving the target "
            f"here first if you have an id or a function: await rekuest.aresolve(target)."
        )

    async def acall(
        self,
        target: "CallTarget",
        *args: Any,  # noqa: ANN401 -- the action's own arguments
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
        policy: "TaskPolicy | None" = None,
        state_requires: "list[StateRequirement] | None" = None,
        on_lost: str | None = None,
        max_resumes: int | None = None,
        inquire_after: float | None = None,
        on_agent: "AgentTarget | None" = None,
        **kwargs: Any,  # noqa: ANN401 -- ditto, by keyword
    ) -> Any:  # noqa: ANN401 -- whatever the action returns
        """Call an action as a root task, through this client's own postman.

        ``policy`` says whether the work agents ask for under it waits for a person:
        ASK (the server's default) holds every child until someone approves it, AUTO
        (auto-mode) lets them run. Unattended runs -- scripts, tests -- pass AUTO.

        ``state_requires`` is what the agent's state must hold for the call to run
        (:class:`~arkitekt_spec.declare.task.StateRequirement`). The agent tests it with
        the task's locks held; when it does not hold, nothing runs and this raises
        :class:`~arkitekt_spec.declare.errors.StatePreconditionFailed`.

        ``on_agent`` is the agent an action is to run on (its id, or a fetched agent) rather
        than whichever agent of it is free. A stateful action needs one: assigned by
        action alone the server refuses it, and this raises
        :class:`~arkitekt_runtime.postmans.errors.StatefulNeedsAgent`. An
        ``Implementation`` (and a function registered on this app, which resolves to
        one) names its agent already, so ``on_agent`` with one is a ``ValueError``.
        """
        self._refuse_in_task("acall")
        from arkitekt_runtime.invoke import _acall

        return await _acall(
            await self.aresolve(target),
            *args,
            postman=self.postman,
            origin=self._origin,
            reference=reference,
            hooks=hooks,
            capture=capture,
            escalate_to_interrupt=escalate_to_interrupt,
            cancel_timeout=cancel_timeout,
            policy=policy,
            state_requires=state_requires,
            on_lost=on_lost,
            max_resumes=max_resumes,
            inquire_after=inquire_after,
            on_agent=on_agent,
            **kwargs,
        )

    def call(
        self,
        target: "CallTarget",
        *args: Any,  # noqa: ANN401 -- the action's own arguments
        on_agent: "AgentTarget | None" = None,
        **kwargs: Any,  # noqa: ANN401 -- ditto, by keyword
    ) -> Any:  # noqa: ANN401 -- whatever the action returns
        """Call an action as a root task, blocking for its result (``on_agent`` as in :meth:`acall`)."""
        self._refuse_in_task("call")
        return unkoil(self.acall, target, *args, on_agent=on_agent, **kwargs)

    async def acall_raw(
        self,
        kwargs: dict[str, Any] | None = None,
        *,
        action: Action | None = None,
        implementation: Implementation | None = None,
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
        policy: "TaskPolicy | None" = None,
        state_requires: "list[StateRequirement] | None" = None,
        on_lost: str | None = None,
        max_resumes: int | None = None,
        inquire_after: float | None = None,
        on_agent: "AgentTarget | None" = None,
    ) -> Any:  # noqa: ANN401 -- the raw backend payload
        """Call with already-serialized arguments, as a root task.

        ``on_agent`` goes with ``action``: this action, on that agent (see :meth:`acall`).
        """
        self._refuse_in_task("acall_raw")
        from arkitekt_runtime.invoke import _acall_raw

        return await _acall_raw(
            postman=self.postman,
            kwargs=kwargs,
            action_id=action.id if action is not None else None,
            implementation_id=implementation.id if implementation is not None else None,
            reference=reference,
            hooks=hooks,
            capture=capture,
            escalate_to_interrupt=escalate_to_interrupt,
            cancel_timeout=cancel_timeout,
            policy=policy,
            state_requires=state_requires,
            on_lost=on_lost,
            max_resumes=max_resumes,
            inquire_after=inquire_after,
            on_agent=on_agent,
        )

    async def aiterate_raw(
        self,
        kwargs: dict[str, Any] | None = None,
        *,
        action: Action | None = None,
        implementation: Implementation | None = None,
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
        policy: "TaskPolicy | None" = None,
        state_requires: "list[StateRequirement] | None" = None,
        on_lost: str | None = None,
        max_resumes: int | None = None,
        inquire_after: float | None = None,
        on_agent: "AgentTarget | None" = None,
    ) -> AsyncGenerator[Any, None]:
        """Stream with already-serialized arguments, as a root task (``on_agent`` as in :meth:`acall_raw`)."""
        self._refuse_in_task("aiterate_raw")
        from arkitekt_runtime.invoke import _aiterate_raw

        async for value in _aiterate_raw(
            postman=self.postman,
            kwargs=kwargs,
            action_id=action.id if action is not None else None,
            implementation_id=implementation.id if implementation is not None else None,
            reference=reference,
            hooks=hooks,
            capture=capture,
            escalate_to_interrupt=escalate_to_interrupt,
            cancel_timeout=cancel_timeout,
            policy=policy,
            state_requires=state_requires,
            on_lost=on_lost,
            max_resumes=max_resumes,
            inquire_after=inquire_after,
            on_agent=on_agent,
        ):
            yield value

    async def aiterate(
        self,
        target: "CallTarget",
        *args: Any,  # noqa: ANN401 -- the action's own arguments
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
        policy: "TaskPolicy | None" = None,
        state_requires: "list[StateRequirement] | None" = None,
        on_lost: str | None = None,
        max_resumes: int | None = None,
        inquire_after: float | None = None,
        on_agent: "AgentTarget | None" = None,
        **kwargs: Any,  # noqa: ANN401 -- ditto, by keyword
    ) -> AsyncGenerator[Any, None]:
        """Stream a generator action's yields, as a root task (``on_agent`` as in :meth:`acall`)."""
        self._refuse_in_task("aiterate")
        from arkitekt_runtime.invoke import _aiterate

        async for value in _aiterate(
            await self.aresolve(target),
            *args,
            postman=self.postman,
            origin=self._origin,
            reference=reference,
            hooks=hooks,
            capture=capture,
            escalate_to_interrupt=escalate_to_interrupt,
            cancel_timeout=cancel_timeout,
            policy=policy,
            state_requires=state_requires,
            on_lost=on_lost,
            max_resumes=max_resumes,
            inquire_after=inquire_after,
            on_agent=on_agent,
            **kwargs,
        ):
            yield value

    def iterate(
        self,
        target: "CallTarget",
        *args: Any,  # noqa: ANN401 -- the action's own arguments
        on_agent: "AgentTarget | None" = None,
        **kwargs: Any,  # noqa: ANN401 -- ditto, by keyword
    ) -> Generator[Any, None, None]:
        """Stream a generator action's yields, as a root task, blocking between them (``on_agent`` as in :meth:`acall`)."""
        self._refuse_in_task("iterate")
        return unkoil_gen(self.aiterate, target, *args, on_agent=on_agent, **kwargs)
