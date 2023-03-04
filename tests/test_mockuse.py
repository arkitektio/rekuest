from rekuest.postmans.utils import mockuse
from rekuest.definition.define import prepare_definition
import asyncio
import pytest
from rekuest.definition.registry import DefinitionRegistry
from rekuest.agents.base import BaseAgent
from rekuest.agents.transport.mock import MockAgentTransport


@pytest.mark.asyncio
async def test_mockuse_assign(simple_registry):
    # THe function we would like to run

    d = DefinitionRegistry()

    def func(hallo: int) -> int:
        """This function

        This function is a test function

        """

        return 1

    # Tasks that are normally done by arkitket
    d.register(func, simple_registry)
    prepare_definition(func, simple_registry)

    agenttransport = MockAgentTransport()
    agent = BaseAgent(transport=agenttransport)

    async def do_func():
        async with mockuse(returns=(1, 2)) as a:
            return await a.aassign(4)

    async with agent:
        what_we_want = asyncio.create_task(do_func())

        result = await what_we_want

    assert result == (1, 2)
