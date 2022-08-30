import pytest
from rekuest.agents.base import BaseAgent
from rekuest.agents.transport.protocols.agent_json import (
    AssignationChangedMessage,
    ProvisionChangedMessage,
)
from rekuest.api.schema import AssignationStatus, ProvisionStatus
from rekuest.messages import Assignation, Provision, Unassignation, Unprovision
from .mocks import MockRekuest
from .funcs import function_with_side_register, function_with_side_register_async

from rekuest.definition.registry import DefinitionRegistry
from rekuest.structures.registry import StructureRegistry
from rekuest.agents.transport.mock import MockAgentTransport

from .structures import SecondObject


@pytest.fixture
def complex_structure_registry():
    structure_registry = StructureRegistry()

    async def expand_second(id):
        return SecondObject(id)

    async def shrink_second(object):
        return object.id

    structure_registry.register_as_structure(
        SecondObject, "hm/second", expand_second, shrink_second
    )
    return structure_registry


@pytest.fixture
def complex_definition_registry(complex_structure_registry):
    definition_registry = DefinitionRegistry(
        structure_registry=complex_structure_registry
    )

    definition_registry.register(function_with_side_register)

    return definition_registry


@pytest.fixture
def complex_definition_registry_async(complex_structure_registry):
    definition_registry = DefinitionRegistry(
        structure_registry=complex_structure_registry
    )

    definition_registry.register(function_with_side_register_async)

    return definition_registry


async def test_agent_assignation():

    mockapp = MockRekuest()

    @mockapp.register()
    async def hallo_world(i: int) -> int:
        """Hallo World

        Hallo world is a mini function

        Args:
            i (int): My little poney

        Returns:
            int: Anoter little int
        """
        return i + 1

    async with mockapp:
        mock_agent = mockapp.agent
        transport = mockapp.agent.transport

        await mock_agent.astart()
        await transport.adelay(Provision(provision="1", template="1"))
        await mock_agent.astep()


        p = await transport.areceive(timeout=1)
        assert isinstance(p, ProvisionChangedMessage)
        assert (
            p.status == ProvisionStatus.ACTIVE
        ), f"The provision should be active {p.message}"

        await transport.adelay(Assignation(provision="1", assignation="1", args=[1]))
        await mock_agent.astep()

        a = await transport.areceive(timeout=1)
        assert isinstance(a, AssignationChangedMessage)
        assert (
            a.status == AssignationStatus.ASSIGNED
        ), f"The assignaiton should be assigned {a.message}"

        a = await transport.areceive(timeout=1)
        assert isinstance(a, AssignationChangedMessage)
        assert (
            a.status == AssignationStatus.RETURNED
        ), f"The assignaiton should have returned {a.message}"
        assert a.returns == [2], f"The provision should have returned {a.message}"


async def test_complex_agent_gen(complex_definition_registry):

    mockapp = MockRekuest(
        definition_registry=complex_definition_registry,
    )

    transport: MockAgentTransport = mockapp.agent.transport
    a: BaseAgent = mockapp.agent

    async with mockapp:

        await a.astart()

        await transport.adelay(Provision(provision="1", template="1"))
        await a.astep()

    
        p = await transport.areceive(timeout=1)
        assert isinstance(p, ProvisionChangedMessage)
        assert (
            p.status == ProvisionStatus.ACTIVE
        ), f"The provision should be active {p.message}"

        await transport.adelay(
            Assignation(
                provision="1",
                assignation="1",
                args=[[1]],
                kwargs={"name": {"hallo": 1}},
            )
        )

        await a.astep()

        a = await transport.areceive(timeout=0.2)

        assert isinstance(a, AssignationChangedMessage)
        assert (
            a.status == AssignationStatus.ASSIGNED
        ), f"The assignaiton should be assigned {a.message}"

        a = await transport.areceive(timeout=0.3)
        assert isinstance(a, AssignationChangedMessage)
        assert (
            a.status == AssignationStatus.YIELD
        ), f"The assignaiton should have returned {a.message}"
        assert a.returns == [
            "tested",
            {"peter": 6},
        ], f"The provision should have returned {a.message}"

        a = await transport.areceive(timeout=1)
        assert isinstance(a, AssignationChangedMessage)
        assert (
            a.status == AssignationStatus.DONE
        ), f"The assignaiton should have been done {a.message}"


async def test_complex_agent_gen_assignation_cancellation(
    complex_definition_registry_async,
):

    mockapp = MockRekuest(
        arkitekt=MockRekuest(
            definition_registry=complex_definition_registry_async,
        )
    )

    transport: MockAgentTransport = mockapp.agent.transport
    agent: BaseAgent = mockapp.agent

    async with mockapp:

        await agent.astart()

        await transport.adelay(Provision(provision="1", template="1"))
        await agent.astep()


        p = await transport.areceive(timeout=1)
        assert isinstance(p, ProvisionChangedMessage)
        assert (
            p.status == ProvisionStatus.ACTIVE
        ), f"The provision should be active {p.message}"

        await transport.adelay(
            Assignation(
                provision="1",
                assignation="1",
                args=[[1]],
                kwargs={"name": {"hallo": 1}},
            )
        )

        await agent.astep()

        a = await transport.areceive(timeout=1)

        assert isinstance(a, AssignationChangedMessage)
        assert (
            a.status == AssignationStatus.ASSIGNED
        ), f"The assignaiton should be assigned {a.message}"

        a = await transport.areceive(timeout=1)
        assert isinstance(a, AssignationChangedMessage)
        assert (
            a.status == AssignationStatus.YIELD
        ), f"The assignaiton should have returned {a.message}"
        assert a.returns == [
            "tested",
            {"peter": 6},
        ], f"The provision should have returned {a.message}"

        await transport.adelay(
            Unassignation(
                provision="1",
                assignation="1",
            )
        )

        await agent.astep()

        a = await transport.areceive(timeout=1)
        assert isinstance(a, AssignationChangedMessage)
        assert (
            a.status == AssignationStatus.CANCELLED
        ), f"The assignaiton should have been cancelled {a.message}"


async def test_complex_agent_gen_provision_cancellation(
    complex_definition_registry_async,
):

    mockapp = MockRekuest(
            definition_registry=complex_definition_registry_async,
    )

    transport: MockAgentTransport = mockapp.agent.transport
    agent: BaseAgent = mockapp.agent

    async with mockapp:

        await agent.astart()

        await transport.adelay(Provision(provision="1", template="1"))
        await agent.astep()


        p = await transport.areceive(timeout=1)
        assert isinstance(p, ProvisionChangedMessage)
        assert (
            p.status == ProvisionStatus.ACTIVE
        ), f"The provision should be active {p.message}"

        await transport.adelay(
            Assignation(
                provision="1",
                assignation="1",
                args=[[1]],
                kwargs={"name": {"hallo": 1}},
            )
        )

        await agent.astep()

        a = await transport.areceive(timeout=1)

        assert isinstance(a, AssignationChangedMessage)
        assert (
            a.status == AssignationStatus.ASSIGNED
        ), f"The assignaiton should be assigned {a.message}"

        a = await transport.areceive(timeout=1)
        assert isinstance(a, AssignationChangedMessage)
        assert (
            a.status == AssignationStatus.YIELD
        ), f"The assignaiton should have returned {a.message}"
        assert a.returns == [
            "tested",
            {"peter": 6},
        ], f"The provision should have returned {a.message}"

        await transport.adelay(
            Unprovision(
                provision="1",
            )
        )

        await agent.astep()

        p = await transport.areceive(timeout=1)
        assert isinstance(p, ProvisionChangedMessage)
        assert (
            p.status == ProvisionStatus.CANCELING
        ), f"The assignaiton should have been cancelled {a.message}"

        a = await transport.areceive(timeout=1)
        assert isinstance(a, AssignationChangedMessage)
        assert (
            a.status == AssignationStatus.CANCELLED
        ), f"The assignaiton should have been cancelled {a.message}"

        p = await transport.areceive(timeout=1)
        assert isinstance(p, ProvisionChangedMessage)
        assert (
            p.status == ProvisionStatus.CANCELLED
        ), f"The assignaiton should have been cancelled {a.message}"
