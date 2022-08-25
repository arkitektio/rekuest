import pytest

from rekuest.messages import Assignation, Reservation
from rekuest.structures.registry import StructureRegistry

from .mocks import MockRequestRath

from rekuest.postmans.transport.mock import (
    MockAutoresolvingPostmanTransport,
    MockPostmanTransport,
)
from rekuest.postmans.stateful import StatefulPostman
from rekuest.postmans.utils import use
import asyncio
from rekuest.api.schema import AssignationStatus, ReservationStatus, afind
from .mocks import MockRekuest


@pytest.fixture
def arkitekt_rath():

    return MockRequestRath()


@pytest.fixture
def mock_autoresolving_postman():

    transport = MockAutoresolvingPostmanTransport()

    postman = StatefulPostman(
        transport=transport,
    )

    return postman


async def test_postman(mock_autoresolving_postman, arkitekt_rath):

    async with StructureRegistry() as s:
        async with arkitekt_rath:
            async with mock_autoresolving_postman:

                node = await afind(package="mock", interface="run_maboy")

                async def test_function():
                    async with use(node=node) as res:
                        return await res.aassign(a=1, b=2)

                returns = await asyncio.wait_for(test_function(), timeout=2)

            assert returns == None, "x should be empty"


async def test_reserve_and_return():

    mock_app = MockRekuest(
        postman=StatefulPostman(transport=MockPostmanTransport(auto_resolve=False))
    )

    t = mock_app.postman.transport

    async with mock_app:

        node = await afind(package="mock", interface="run_maboy")

        async def test_function():
            async with use(node=node) as res:
                return await res.aassign(a=1, b=2)

        reserve_task = asyncio.create_task(test_function())

        res = await t.areceive(timeout=1)
        assert isinstance(res, Reservation), "res should be a Reservation"
        await t.adelay(res.update(status=ReservationStatus.ACTIVE))

        ass = await t.areceive(timeout=1)
        assert isinstance(ass, Assignation), "ass should be an Assignation"

        await t.adelay(ass.update(status=AssignationStatus.RETURNED, returns=[]))

        x = await asyncio.wait_for(reserve_task, timeout=2)
        assert x == None, "x should be None"


async def test_reserve_provide_and_return():

    mock_app = MockRekuest(
        postman=StatefulPostman(transport=MockPostmanTransport(auto_resolve=False))
    )

    t = mock_app.postman.transport

    async with mock_app:

        node = await afind(package="mock", interface="run_maboy")

        async def test_function():
            async with use(node=node) as res:
                return await res.aassign(a=1, b=2)

        reserve_task = asyncio.create_task(test_function())

        res = await t.areceive(timeout=1)
        assert isinstance(res, Reservation), "res should be a Reservation"
        await t.adelay(res.update(status=ReservationStatus.PROVIDING))

        await asyncio.sleep(0.1)

        await t.adelay(res.update(status=ReservationStatus.ACTIVE))

        ass = await t.areceive(timeout=1)
        assert isinstance(ass, Assignation), "ass should be an Assignation"

        await t.adelay(ass.update(status=AssignationStatus.RETURNED, returns=[]))

        x = await asyncio.wait_for(reserve_task, timeout=2)
        assert x == None, "x should be None"
