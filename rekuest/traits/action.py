"""Traits for actions , so that we can use them as reservable context"""

from rekuest.protocol.schema import ActionKind
from koil.composition.base import KoiledModel
from rath.origin import ContextBound
import typing
import logging

logger = logging.getLogger(__name__)


class Callable(ContextBound, KoiledModel):
    """A class to reserve a action in the graph.

    Calling an action directly goes through the client it was fetched with, which makes a
    root task -- so inside a running task it refuses, and the in-task spelling is
    ``task.call(action, ...)``.

    ``ContextBound`` comes **first**, and the order is load-bearing: ``KoiledModel``
    defines its own ``model_post_init`` (pydantic's ``init_private_attributes``) which
    does not chain to ``super()``, so with ``KoiledModel`` ahead of it
    ``ContextBound.model_post_init`` never runs and :meth:`bound_client` silently
    returns ``None`` instead of the client the action was fetched with. Nothing raises;
    the origin is simply dropped. ``tests/test_action_trait.py`` pins the order.
    """

    def get_action_kind(self) -> str:
        """Get the kind of the action.
        Returns:
            str: The kind of the action.
        """
        return getattr(self, "kind")

    def _client(self, method: str) -> typing.Any:  # noqa: ANN401 -- a Rekuest
        """The client this action was fetched with.

        A fetched action remembers its origin (:class:`rath.origin.ContextBound`), so it
        needs no ambient lookup. One built by hand -- ``Action.model_construct(...)``, or a
        plain ``model_validate`` with no validation context -- has none, and must say so
        rather than failing later on ``None``.
        """
        client = self.bound_client()
        if client is None:
            raise ValueError(
                f"This action was not fetched through a Rekuest client, so it does not "
                f"know one to {method} through. Call it as rekuest.{method}(action, ...)."
            )
        return client

    def iterate(
        self, *args: typing.Any, **kwargs: typing.Any
    ) -> typing.Iterator[typing.Any]:
        """Stream this action's yields, through the client it was fetched with."""
        assert self.get_action_kind() == ActionKind.GENERATOR, (
            "Action kind must be GENERATOR to use iterate."
        )
        return self._client("iterate").iterate(self, *args, **kwargs)

    def call(self, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        """Call this action, through the client it was fetched with."""
        if self.get_action_kind() == ActionKind.GENERATOR:
            logger.warning(
                "Action kind is Generator, but call is being used. This will exhaust the generator and return the last value. Consider using iterate instead."
            )
        return self._client("call").call(self, *args, **kwargs)

    def aiterate(
        self, *args: typing.Any, **kwargs: typing.Any
    ) -> typing.AsyncIterator[typing.Any]:
        """Stream this action's yields, through the client it was fetched with."""
        assert self.get_action_kind() == ActionKind.GENERATOR, (
            "Action kind must be GENERATOR to use aiterate."
        )
        return self._client("aiterate").aiterate(self, *args, **kwargs)

    async def acall(self, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        """Call this action, through the client it was fetched with."""
        assert self.get_action_kind() == ActionKind.FUNCTION, (
            "Action kind must be FUNCTION to use acall."
        )
        return await self._client("acall").acall(self, *args, **kwargs)

    def __call__(self, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        """Call the action, or stream it if it is a generator."""
        if self.get_action_kind() == ActionKind.GENERATOR:
            return self.iterate(*args, **kwargs)

        return self.call(*args, **kwargs)
