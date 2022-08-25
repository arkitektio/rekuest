from typing import Any
from qtpy import QtCore
from rekuest.agents.transport.base import AgentTransport
from ..definition.registry import DefaultActorBuilder
from rekuest.messages import Provision
from koil.qt import QtCoro
from rekuest.actors.functional import FunctionalFuncActor


class QtInLoopBuilder(QtCore.QObject):
    """A function that takes a provision and an agent and returns an actor.

    The actor produces by this builder will be running in the same thread as the
    koil instance (aka, the thread that called the builder).

    Args:
        QtCore (_type_): _description_
    """

    def __init__(self, assign=None, *args, parent=None, **actor_kwargs) -> None:
        super().__init__(*args, parent=parent)
        self.coro = QtCoro(
            lambda f, *args, **kwargs: assign(*args, **kwargs), autoresolve=True
        )
        self.provisions = {}
        self.actor_kwargs = actor_kwargs

    async def on_assign(self, *args, **kwargs) -> None:
        return await self.coro.acall(*args, **kwargs)

    def build_actor(self, provision: Provision, transport: AgentTransport) -> Any:
        try:
            ac = FunctionalFuncActor(
                provision=provision,
                transport=transport,
                assign=self.on_assign,
                **self.actor_kwargs
            )
            return ac
        except Exception as e:
            raise e


class QtPassFutureBuilder(QtCore.QObject):
    """A function that takes a provision and an agent and returns an actor.

    The actor produces by this builder will be running in the same thread as the
    koil instance (aka, the thread that called the builder).

    Args:
        QtCore (_type_): _description_
    """

    def __init__(self, assign=None, *args, parent=None, **actor_kwargs) -> None:
        super().__init__(*args, parent=parent)
        self.coro = QtCoro(
            lambda f, *args, **kwargs: assign(f, *args, **kwargs), autoresolve=False
        )
        self.provisions = {}
        self.actor_kwargs = actor_kwargs

    async def on_assign(self, *args, **kwargs) -> None:
        return await self.coro.acall(*args, **kwargs)

    def build_actor(self, provision: Provision, transport: AgentTransport) -> Any:
        try:
            ac = FunctionalFuncActor(
                provision=provision,
                transport=transport,
                assign=self.on_assign,
                **self.actor_kwargs
            )
            return ac
        except Exception as e:
            raise e


class QtInLoopActorBuilder(DefaultActorBuilder):
    def actify(self, *args, **kwargs):
        return QtInLoopBuilder(*args, **kwargs).build_actor


class QtPassFutureActorBuilder(DefaultActorBuilder):
    def define(self, *args, **kwargs):
        return super().define(*args, **kwargs, omitfirst=1)

    def actify(self, *args, **kwargs):
        return QtPassFutureBuilder(*args, **kwargs).build_actor
