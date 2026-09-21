"""The base client for rekuest"""

from typing import TypeVar
from rekuest.protocol.types import AnyFunction
from rekuest.client.rath import RekuestRath
from rekuest.api.schema import RekuestApi
from rekuest.postmans.types import Postman
from koil import unkoil, unkoil_gen
from koil.composition import Composition
from rath.origin import origin_context
from rath.turms.funcs import TOperation
from pydantic import Field

from typing import (
    Any,
)
from collections.abc import AsyncGenerator, Generator
from rekuest.structures.registry import StructureRegistry


T = TypeVar("T", bound=AnyFunction)


class Rekuest(Composition, RekuestApi):
    """The rekuest client: every rekuest operation is a method of it, and it calls
    actions (``rekuest.call(...)``).

    A service builds it; it knows nothing about any agent. An action asks for it by
    annotation (``rekuest: Rekuest``) and is handed :meth:`for_task`: a view whose
    calls go over the socket of the agent running the task, as children of it.
    """

    rath: RekuestRath
    postman: Postman
    structure_registry: StructureRegistry = Field(exclude=True)
    """The registry of the run this client belongs to: what its calls (de)serialize with."""
    parent: Any = Field(default=None, exclude=True)
    """The assignment this view calls on behalf of; set on a per-task view only."""

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

    def for_task(self, task: Any) -> "Rekuest":  # noqa: ANN401
        """A view of this client whose calls are children of ``task``.

        They go over the socket of the agent running the task (the only transport
        that carries a parent) and are parented to the task's assignment. rekuest
        hands an injected ``rekuest: Rekuest`` out through this. A task no agent
        runs (``Task.local()``) gets the client itself: its calls are roots.
        """
        agent = getattr(task, "agent", None)
        if agent is None:
            return self
        return self.model_copy(
            update={"parent": task.assignment, "postman": agent.caller_postman}
        )

    def _call_options(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        kwargs.setdefault("structure_registry", self.structure_registry)
        return self._raw_options(kwargs)

    async def aresolve(self, target: Any) -> Any:  # noqa: ANN401
        """What to call: an action, an implementation, an action id, or a function
        registered on this client's app (its implementation there)."""
        from rath.scalars import ID

        from rekuest.api.schema import Action, Implementation
        from rekuest.register import WrappedFunction

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

    async def acall(self, target: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call an action through this client (a child of the task, on a task view)."""
        from rekuest.client.remote import acall

        return await acall(
            await self.aresolve(target), *args, **self._call_options(kwargs)
        )

    def call(self, target: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call an action through this client (a child of the task, on a task view)."""
        return unkoil(self.acall, target, *args, **kwargs)

    def _raw_options(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self.parent is not None:
            kwargs.setdefault("parent", self.parent)
        kwargs.setdefault("postman", self.postman)
        return kwargs

    async def acall_raw(self, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call with already-serialized arguments (see ``rekuest.client.remote.acall_raw``)."""
        from rekuest.client.remote import acall_raw

        return await acall_raw(**self._raw_options(kwargs))

    async def aiterate_raw(self, **kwargs: Any) -> Any:  # noqa: ANN401
        """Stream with already-serialized arguments (see ``rekuest.client.remote.aiterate_raw``)."""
        from rekuest.client.remote import aiterate_raw

        async for value in aiterate_raw(**self._raw_options(kwargs)):
            yield value

    async def aiterate(self, target: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Stream an action's yields through this client."""
        from rekuest.client.remote import aiterate

        async for value in aiterate(
            await self.aresolve(target), *args, **self._call_options(kwargs)
        ):
            yield value

    def iterate(self, target: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Stream an action's yields through this client."""
        from koil import unkoil_gen

        return unkoil_gen(self.aiterate, target, *args, **kwargs)

