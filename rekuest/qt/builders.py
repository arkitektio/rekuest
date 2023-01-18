from typing import Any
from qtpy import QtCore
from rekuest.agents.transport.base import AgentTransport
from rekuest.api.schema import TemplateFragment
from rekuest.messages import Provision
from koil.qt import QtCoro
from rekuest.actors.functional import FunctionalFuncActor
from rekuest.rath import RekuestRath
from qtpy import QtWidgets
from rekuest.definition.registry import ActorBuilder
from rekuest.definition.define import prepare_definition, DefinitionInput



class QtInLoopBuilder(QtCore.QObject):
    """A function that takes a provision and an actor transport and returns an actor.

    The actor produces by this builder will be running in the same thread as the
    koil instance (aka, the thread that called the builder).

    Args:
        QtCore (_type_): _description_
    """

    def __init__(
        self, assign=None, *args, parent=None, structure_registry=None, **actor_kwargs
    ) -> None:
        super().__init__(*args, parent=parent)
        self.coro = QtCoro(
            lambda *args, **kwargs: assign(*args, **kwargs), autoresolve=True
        )
        self.provisions = {}
        self.structure_registry = structure_registry
        self.actor_kwargs = actor_kwargs

    async def on_assign(self, *args, **kwargs) -> None:
        return await self.coro.acall(*args, **kwargs)

    async def on_provide(self, provision: Provision) -> Any:
        return None

    async def on_unprovide(self, provision: Provision) -> Any:
        return None

    def build(
        self,
        provision: Provision,
        transport: AgentTransport,
        definition: DefinitionInput,
    ) -> Any:
        try:
            ac = FunctionalFuncActor(
                definition=definition,
                provision=provision,
                structure_registry=self.structure_registry,
                transport=transport,
                assign=self.on_assign,
                on_provide=self.on_provide,
                on_unprovide=self.on_unprovide,
            )
            return ac
        except Exception as e:
            raise e


def qtinloopactifier(
    function, structure_registry, parent: QtWidgets.QWidget = None, **kwargs
) -> ActorBuilder:
    """Qt Actifier

    The qt actifier wraps a function and returns a builder that will create an actor
    that runs in the same thread as the Qt instance, enabling the use of Qt widgets
    and signals.
    """

    in_loop_instance = QtInLoopBuilder(
        parent=parent, assign=function, structure_registry=structure_registry
    )
    definition = prepare_definition(function, structure_registry)

    def builder(
        provision: Provision,
        transport: AgentTransport,
    ) -> Any:

        return in_loop_instance.build(
            provision, transport, definition
        )  # build an actor for this inloop instance

    builder.__definition__ = definition

    return builder
