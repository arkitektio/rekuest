from pydantic import BaseModel
from rekuest.definition.define import prepare_definition
from rekuest.definition.validate import auto_validate
from rekuest.api.schema import (
    NodeFragment,
    MessageInput,
    MessageKind,
    DefinitionFragment,
)
from rekuest.messages import Provision, Assignation
from rekuest.actors.base import Actor
from rekuest.agents.transport.mock import MockAgentTransport
from .funcs import plain_basic_function
import pytest
from .registries import simple_registry
from rekuest.actors.actify import reactify
import asyncio
from rekuest.agents.transport.protocols.agent_json import (
    ProvisionChangedMessage,
    AssignationChangedMessage,
    ProvisionStatus,
    AssignationStatus,
)
from rekuest.api.schema import DefinitionInput, NodeKindInput


@pytest.mark.actor
def test_reactify_instatiation(simple_registry):

    actorBuilder = reactify(plain_basic_function, simple_registry)

    provision = Provision(provision=1, guardian=1, user=1)

    actor = actorBuilder(provision=provision, transport=MockAgentTransport())
    assert actor is not None, "Actor should be instatiated"


class MockActor(Actor):
    definition = DefinitionInput(
        name="mock", description="mock", kind=NodeKindInput.GENERATOR
    )

    async def on_provide(self, provision: Provision):
        return None

    async def on_assign(self, assignation: Assignation):
        print("This is running")
        self.transport.change_assignation(
            assignation.assignation, status=AssignationStatus.DONE, returns=[1]
        )
        return None


class MockErrorActor(Actor):
    definition = DefinitionInput(
        name="mock", description="mock", kind=NodeKindInput.GENERATOR
    )

    async def on_provide(self, provision: Provision):
        raise Exception("Not implemented")
        return None

    async def on_assign(self, assignation: Assignation):
        print("Hallo")
        self.transport.change_assignation(
            assignation.assignation, status=AssignationStatus.CRITICAL
        )
        return None


@pytest.mark.asyncio
@pytest.mark.actor
async def test_provide_actor():

    provision = Provision(provision=1, guardian=1, user=1)
    assignation = Assignation(assignation=1, user=1, provision=1)

    async with MockAgentTransport() as transport:

        async with MockActor(provision=provision, transport=transport) as actor:

            await actor.provide()
            x = await transport.areceive(timeout=1)
            assert isinstance(x, ProvisionChangedMessage)
            assert x.status == ProvisionStatus.ACTIVE


@pytest.mark.asyncio
@pytest.mark.actor
async def test_provide_actor_error():

    provision = Provision(provision=1, guardian=1, user=1)
    assignation = Assignation(assignation=1, user=1, provision=1)

    async with MockAgentTransport() as transport:

        async with MockErrorActor(provision=provision, transport=transport) as actor:

            await actor.provide()
            x = await transport.areceive(timeout=1)
            assert isinstance(x, ProvisionChangedMessage)
            assert x.status == ProvisionStatus.CRITICAL


@pytest.mark.asyncio
@pytest.mark.actor
async def test_assign_actor():

    provision = Provision(provision=1, guardian=1, user=1)
    assignation = Assignation(assignation=1, user=1, provision=1, args=[], returns=[])

    async with MockAgentTransport() as transport:

        async with MockActor(provision=provision, transport=transport) as actor:

            await actor.provide()
            x = await transport.areceive(timeout=1)
            assert isinstance(x, ProvisionChangedMessage)
            assert x.status == ProvisionStatus.ACTIVE


@pytest.mark.asyncio
@pytest.mark.actor
async def test_assign_actor_error():

    provision = Provision(provision=1, guardian=1, user=1)
    assignation = Assignation(assignation=1, user=1, provision=1)

    async with MockAgentTransport() as transport:

        async with MockActor(provision=provision, transport=transport) as actor:

            await actor.provide()
            x = await transport.areceive(timeout=1)
            assert isinstance(x, ProvisionChangedMessage)
            assert x.status == ProvisionStatus.ACTIVE
