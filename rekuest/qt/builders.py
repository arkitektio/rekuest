from typing import Any, Tuple
from qtpy import QtCore
from rekuest.agents.transport.base import AgentTransport
from rekuest.messages import Provision
from koil.qt import QtCoro
from rekuest.actors.functional import FunctionalFuncActor
from qtpy import QtWidgets
from rekuest.definition.registry import ActorBuilder
from rekuest.definition.define import prepare_definition, DefinitionInput
from rekuest.actors.types import ActorBuilder, Passport
from rekuest.collection.collector import ActorCollector
from rekuest.actors.transport.types import ActorTransport
import inspect
from rekuest.actors.errors import ActifierException


class QtInLoopBuilder(QtCore.QObject):
    """A function that takes a provision and an actor transport and returns an actor.

    The actor produces by this builder will be running in the same thread as the
    koil instance (aka, the thread that called the builder).

    Args:
        QtCore (_type_): _description_
    """

    def __init__(
        self,
        assign=None,
        *args,
        parent=None,
        structure_registry=None,
        definition=None,
        **actor_kwargs,
    ) -> None:
        super().__init__(*args, parent=parent)
        self.coro = QtCoro(
            lambda *args, **kwargs: assign(*args, **kwargs), autoresolve=True
        )
        self.provisions = {}
        self.structure_registry = structure_registry
        self.actor_kwargs = actor_kwargs
        self.definition = definition

    async def on_assign(self, *args, **kwargs) -> None:
        return await self.coro.acall(*args, **kwargs)

    async def on_provide(self, provision: Provision) -> Any:
        return None

    async def on_unprovide(self) -> Any:
        return None

    def build(self, *args, **kwargs) -> Any:
        try:
            ac = FunctionalFuncActor(
                *args,
                **kwargs,
                structure_registry=self.structure_registry,
                assign=self.on_assign,
                on_provide=self.on_provide,
                on_unprovide=self.on_unprovide,
                definition=self.definition,
            )
            return ac
        except Exception as e:
            raise e


class QtFutureBuilder(QtCore.QObject):
    """A function that takes a provision and an actor transport and returns an actor.

    The actor produces by this builder will be running in the same thread as the
    koil instance (aka, the thread that called the builder).

    Args:
        QtCore (_type_): _description_
    """

    def __init__(
        self,
        assign=None,
        *args,
        parent=None,
        structure_registry=None,
        definition=None,
        **actor_kwargs,
    ) -> None:
        super().__init__(*args, parent=parent)
        self.coro = QtCoro(
            lambda *args, **kwargs: assign(*args, **kwargs), autoresolve=False
        )
        self.provisions = {}
        self.structure_registry = structure_registry
        self.actor_kwargs = actor_kwargs
        self.definition = definition

    async def on_assign(self, *args, **kwargs) -> None:
        x = await self.coro.acall(*args, **kwargs)
        return x

    async def on_provide(self, provision: Provision) -> Any:
        return None

    async def on_unprovide(self) -> Any:
        return None

    def build(self, *args, **kwargs) -> Any:
        try:
            ac = FunctionalFuncActor(
                *args,
                **kwargs,
                structure_registry=self.structure_registry,
                assign=self.on_assign,
                on_provide=self.on_provide,
                on_unprovide=self.on_unprovide,
                definition=self.definition,
            )
            return ac
        except Exception as e:
            raise e


def qtinloopactifier(
    function, structure_registry, parent: QtWidgets.QWidget = None, **kwargs
) -> Tuple[DefinitionInput, ActorBuilder]:
    """Qt Actifier

    The inloop actifier ensures the actor is running in the same thread as the Qt
    application, and will return the result of the function call to the actor.
    """

    definition = prepare_definition(function, structure_registry, **kwargs)

    in_loop_instance = QtInLoopBuilder(
        parent=parent,
        assign=function,
        structure_registry=structure_registry,
        definition=definition,
    )

    def builder(*args, **kwargs) -> Any:
        return in_loop_instance.build(
            *args, **kwargs
        )  # build an actor for this inloop instance

    return definition, builder


def predicate_first_param_is_future(function):
    params = inspect.signature(function).parameters
    future_param_name = list(params.keys())[0]
    return future_param_name == "qtfuture"


def qtwithfutureactifier(
    function, structure_registry, parent: QtWidgets.QWidget = None, **kwargs
) -> ActorBuilder:
    """Qt Actifier

    The qt actifier ensures the actor is running in the same thread as the Qt instance,
    and will pass a QT future to the actor, which can be resolved by the actor at any
    time.


    """

    if not predicate_first_param_is_future(function):
        raise ActifierException(
            f"We expect the first parameter to be called 'qtfuture', to use the qtwithfutureactifier. Just for convention sake. Please check {function}"
        )

    definition = prepare_definition(function, structure_registry, omitfirst=1, **kwargs)

    in_loop_instance = QtFutureBuilder(
        parent=parent,
        assign=function,
        structure_registry=structure_registry,
        definition=definition,
    )

    def builder(*args, **kwargs) -> Any:
        return in_loop_instance.build(
            *args, **kwargs
        )  # build an actor for this inloop instance

    return definition, builder
