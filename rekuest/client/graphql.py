"""The rekuest GraphQL operations over one rath, for rekuest's own internals.

The GraphQL postman is part of a :class:`Rekuest` client and holds its rath, not
the client. It reaches the operations through this: the same generated
``RekuestApi`` methods, bound to that rath. It executes them the way
:class:`Rekuest` does; the four methods are spelled out here as well because it
is not a client (no postman) and cannot be one. The agent has no use for it: its
registration, state, dependencies and shelve all go over its socket.
"""

from collections.abc import AsyncGenerator, Generator
from typing import Any

from koil import unkoil, unkoil_gen
from rath.origin import origin_context
from rath.turms.funcs import TOperation

from rekuest.api.schema import RekuestApi
from rekuest.client.rath import RekuestRath


class RekuestGraphQL(RekuestApi):
    """``RekuestApi`` over a given rath."""

    def __init__(self, rath: RekuestRath) -> None:
        self.rath = rath

    def get(self, key: type) -> None:
        """Answers for nothing: it is not a client, only operations over a rath.

        What it fetches keeps it as its origin (:meth:`aexecute`), and an origin
        is asked ``get(cls)``; this says "none" instead of failing.
        """
        return None

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


__all__ = ["RekuestGraphQL"]
