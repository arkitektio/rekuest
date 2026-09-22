"""The base client for rekuest"""

from typing import TypeVar
from rekuest.protocol.types import AnyFunction
from rekuest.client.rath import RekuestRath
from rekuest.api.schema import Action, Implementation, RekuestApi
from rekuest.postmans.types import Postman
from rekuest.register import WrappedFunction
from rath.scalars import ID
from koil import unkoil, unkoil_gen
from koil.composition import Composition
from rath.origin import origin_context
from rath.task import current_task
from rath.turms.funcs import TOperation
from pydantic import Field

from typing import (
    Any,
)
from collections.abc import AsyncGenerator, Generator
from rekuest.errors import RootOnlyCallError
from rekuest.protocol.schema import HookInput
from rekuest.structures.registry import StructureRegistry

#: What :meth:`Rekuest.aresolve` accepts: a fetched model, an id, or a function this app
#: registered. Only the client can turn the last two into the first -- that takes GraphQL.
CallTarget = Action | Implementation | WrappedFunction[Any, Any] | ID | str


T = TypeVar("T", bound=AnyFunction)


class Rekuest(Composition, RekuestApi):
    """The rekuest client: every rekuest operation is a method of it, and it calls
    actions (``rekuest.call(...)``).

    A service builds it; it knows nothing about any agent. An action asks for it by
    annotation (``rekuest: Rekuest``) and is handed this one shared instance.

    **Its calls are roots.** A call through this client goes over its own postman with no
    parent, so inside a running task it refuses -- a call made then is that task's child,
    and the task is what knows the socket and the assignment to make it one. Look the
    action up here (:meth:`aresolve`, :meth:`afind`) and call it through the task:
    ``await task.acall(await rekuest.aresolve(target), ...)``. Only the six call methods
    refuse; every lookup, query and mutation works inside a task as before.
    """

    rath: RekuestRath
    postman: Postman
    structure_registry: StructureRegistry = Field(exclude=True)
    """The registry of the run this client belongs to: what its calls (de)serialize with."""

    def _serialize(self, operation: type[TOperation], variables: dict[str, Any]) -> dict[str, Any]:
        return operation.Arguments(**variables).model_dump(by_alias=True, exclude_unset=True)

    def execute(self, operation: type[TOperation], variables: dict[str, Any]) -> TOperation:
        """Executes a query or mutation in a blocking way."""
        return unkoil(self.aexecute, operation, variables)

    async def aexecute(self, operation: type[TOperation], variables: dict[str, Any]) -> TOperation:
        """Executes a query or mutation in a non-blocking way."""
        x = await self.rath.aquery(operation.Meta.document, self._serialize(operation, variables))
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

    async def acall(
        self,
        target: "CallTarget",
        *args: Any,  # noqa: ANN401 -- the action's own arguments
        reference: str | None = None,
        hooks: list[HookInput] | None = None,
        capture: bool = False,
        escalate_to_interrupt: bool = False,
        cancel_timeout: float | None = None,
        **kwargs: Any,  # noqa: ANN401 -- ditto, by keyword
    ) -> Any:  # noqa: ANN401 -- whatever the action returns
        """Call an action as a root task, through this client's own postman."""
        self._refuse_in_task("acall")
        from rekuest.invoke import _acall

        return await _acall(
            await self.aresolve(target),
            *args,
            postman=self.postman,
            structure_registry=self.structure_registry,
            reference=reference,
            hooks=hooks,
            capture=capture,
            escalate_to_interrupt=escalate_to_interrupt,
            cancel_timeout=cancel_timeout,
            **kwargs,
        )

    def call(
        self,
        target: "CallTarget",
        *args: Any,  # noqa: ANN401 -- the action's own arguments
        **kwargs: Any,  # noqa: ANN401 -- ditto, by keyword
    ) -> Any:  # noqa: ANN401 -- whatever the action returns
        """Call an action as a root task, blocking for its result."""
        self._refuse_in_task("call")
        return unkoil(self.acall, target, *args, **kwargs)

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
    ) -> Any:  # noqa: ANN401 -- the raw backend payload
        """Call with already-serialized arguments, as a root task."""
        self._refuse_in_task("acall_raw")
        from rekuest.invoke import _acall_raw

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
    ) -> AsyncGenerator[Any, None]:
        """Stream with already-serialized arguments, as a root task."""
        self._refuse_in_task("aiterate_raw")
        from rekuest.invoke import _aiterate_raw

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
        **kwargs: Any,  # noqa: ANN401 -- ditto, by keyword
    ) -> AsyncGenerator[Any, None]:
        """Stream a generator action's yields, as a root task."""
        self._refuse_in_task("aiterate")
        from rekuest.invoke import _aiterate

        async for value in _aiterate(
            await self.aresolve(target),
            *args,
            postman=self.postman,
            structure_registry=self.structure_registry,
            reference=reference,
            hooks=hooks,
            capture=capture,
            escalate_to_interrupt=escalate_to_interrupt,
            cancel_timeout=cancel_timeout,
            **kwargs,
        ):
            yield value

    def iterate(
        self,
        target: "CallTarget",
        *args: Any,  # noqa: ANN401 -- the action's own arguments
        **kwargs: Any,  # noqa: ANN401 -- ditto, by keyword
    ) -> Generator[Any, None, None]:
        """Stream a generator action's yields, as a root task, blocking between them."""
        self._refuse_in_task("iterate")
        return unkoil_gen(self.aiterate, target, *args, **kwargs)

