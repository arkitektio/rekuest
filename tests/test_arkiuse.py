from rekuest.postmans.utils import arkiuse
from rekuest.definition.define import prepare_definition
from rekuest.postmans.transport.mock import MockPostmanTransport
from rekuest.postmans.stateful import StatefulPostman
import asyncio
from rekuest.postmans.transport.protocols.postman_json import (
    ReserveSubUpdate,
    ReservePub,
    AssignPub,
    AssignSubUpdate,
)
from rekuest.api.schema import NodeFragment, ReservationStatus, AssignationStatus
import pytest


# THe function we would like to run
def func(hallo: int) -> int:
    """This function

    This function is a test function

    """

    return 1


@pytest.mark.asyncio
async def test_arkiuse(simple_registry):
    # Tasks that are normally done by arkitket
    x = prepare_definition(func, simple_registry)

    
    defi = NodeFragment(id=1, hash="oinsoinsoins", **x.dict())

    transport = MockPostmanTransport()

    postman = StatefulPostman(transport=transport)

    async def do_func():
        async with arkiuse(
            definition=defi, postman=postman, structure_registry=simple_registry
        ) as a:
            return await a.aassign(4)

    async with postman:
        what_we_want = asyncio.create_task(do_func())

        res = await transport.areceive(timeout=1)
        assert isinstance(res, ReservePub)

        # mimic serverside

        await transport.adelay(
            ReserveSubUpdate(
                reservation=1,  # this is the reservation id (fake server)
                status=ReservationStatus.ACTIVE,
                reference=res.reference,
                node=res.node,
            )
        )

        ass = await transport.areceive(timeout=1)
        assert isinstance(ass, AssignPub)

        await transport.adelay(
            AssignSubUpdate(
                assignation=1,  # this is the assignation id (fake server)
                reference=ass.reference,
                returns=["1"],
                status=AssignationStatus.RETURNED,  # this is the assignation id (fake server)
            )
        )

        result = await what_we_want

    assert result, "The result should exist"


@pytest.mark.asyncio
async def test_arkiuse_state_hook(simple_registry):
    # Tasks that are normally done by arkitket
    x = prepare_definition(func, simple_registry)
    defi = NodeFragment(id=1, hash="oinsoinsoins", **x.dict())

    transport = MockPostmanTransport()

    postman = StatefulPostman(transport=transport)

    state = None

    async def do_func():
        async def state_hook(s):
            nonlocal state
            state = s

        async with arkiuse(
            definition=defi,
            postman=postman,
            structure_registry=simple_registry,
            state_hook=state_hook,
        ) as a:
            return await a.aassign(4)

    async with postman:
        what_we_want = asyncio.create_task(do_func())

        res = await transport.areceive(timeout=1)
        assert isinstance(res, ReservePub)

        # mimic serverside

        await transport.adelay(
            ReserveSubUpdate(
                reservation=1,  # this is the reservation id (fake server)
                status=ReservationStatus.ACTIVE,
                reference=res.reference,
                node=res.node,
            )
        )

        await asyncio.sleep(0.1)
        # TODO: this is a hack, we should have a better way to wait for the state hook to be called
        assert state == ReservationStatus.ACTIVE, "The state should be active"

        ass = await transport.areceive(timeout=1)
        assert isinstance(ass, AssignPub)

        await transport.adelay(
            AssignSubUpdate(
                assignation=1,  # this is the assignation id (fake server)
                reference=ass.reference,
                returns=["1"],
                status=AssignationStatus.RETURNED,  # this is the assignation id (fake server)
            )
        )

        result = await what_we_want

    assert result, "The result should exist"
