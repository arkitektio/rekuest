from rekuest.postmans.utils import definitionmockuse
from rekuest.definition.define import prepare_definition
import asyncio
import pytest
from rekuest.definition.registry import DefinitionRegistry
from rekuest.agents.base import BaseAgent
from rekuest.agents.transport.mock import MockAgentTransport


@pytest.mark.asyncio
async def test_definitionmockuse_assign(simple_registry):
    # THe function we would like to run

    d = DefinitionRegistry()

    def func(hallo: int) -> int:
        """This function

        This function is a test function

        """

        return 1

    # Tasks that are normally done by arkitket
    d.register(func, simple_registry)
    x = prepare_definition(func, simple_registry)

    agenttransport = MockAgentTransport()
    agent = BaseAgent(transport=agenttransport)

    async def do_func():
        async with definitionmockuse(
            definition=x, assign_sleep=0.1, reserve_sleep=0.1, unreserve_sleep=0.1
        ) as a:
            return await a.aassign(4)

    async with agent:
        what_we_want = asyncio.create_task(do_func())

        result = await what_we_want

    assert len(result) == 1
