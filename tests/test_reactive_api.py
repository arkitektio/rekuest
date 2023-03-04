from rekuest.actors.reactive.api import useUser, useGuardian
from rekuest.actors.contexts import AssignationContext, ProvisionContext
from rekuest.messages import Assignation, Provision
from rekuest.agents.transport.mock import MockAgentTransport
import pytest
import random
import asyncio
import time
from koil.helpers import run_spawned


def function():
    return int(useUser())


def guardian_func():
    return useGuardian()


def test_reactive_assignation_api():
    assignation = Assignation(assignation=444, user=1, guardian=1)
    transport = MockAgentTransport()
    with AssignationContext(assignation, transport):
        assert function() == 1, "Should be able to use functional api"


def test_reactive_provision_api():
    provision = Provision(provision=444, user=1, guardian=1)
    transport = MockAgentTransport()

    with ProvisionContext(provision, transport):
        assert guardian_func() == "1", "Should be able to use functional api"


@pytest.mark.asyncio
async def test_reactive_assignation_api_async():
    random_sleep = [random.randint(1, 10) for i in range(10)]

    transport = MockAgentTransport()

    async def async_context(sleep_interval):
        assignation = Assignation(assignation=444, user=sleep_interval, guardian=1)

        with AssignationContext(assignation, transport):
            await asyncio.sleep(sleep_interval * 0.03)
            return function()

    numbers = await asyncio.gather(
        *[async_context(sleep_interval) for sleep_interval in random_sleep]
    )
    assert (
        numbers == random_sleep
    ), "Should be able to retrieve correct user id for context vars in async"


@pytest.mark.asyncio
async def test_reactive_assignation_api_threaded_async():
    random_sleep = [random.randint(1, 10) for i in range(10)]

    transport = MockAgentTransport()

    def function():
        time.sleep(0.01)
        return int(useUser())

    async def async_context(sleep_interval):
        assignation = Assignation(assignation=444, user=sleep_interval, guardian=1)

        with AssignationContext(assignation, transport):
            await asyncio.sleep(sleep_interval * 0.03)
            return await run_spawned(function, pass_context=True)

    numbers = await asyncio.gather(
        *[async_context(sleep_interval) for sleep_interval in random_sleep]
    )
    assert (
        numbers == random_sleep
    ), "Should be able to retrieve correct user id for context vars in koiled context"
