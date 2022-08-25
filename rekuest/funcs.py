from rekuest.rath import RekuestRath, current_rekuest_rath
from koil import unkoil
from koil.helpers import unkoil_gen


def execute(operation, variables, rath: RekuestRath = None):
    return unkoil(aexecute, operation, variables, rath)


async def aexecute(operation, variables, rath: RekuestRath = None):
    rath = rath or current_rekuest_rath.get()
    x = await rath.aquery(
        operation.Meta.document, operation.Arguments(**variables).dict(by_alias=True)
    )
    return operation(**x.data)


def subscribe(operation, variables, rath: RekuestRath = None):
    return unkoil_gen(asubscribe, operation, variables, rath)


async def asubscribe(operation, variables, rath: RekuestRath = None):
    rath = rath or current_rekuest_rath.get()
    async for event in rath.asubscribe(
        operation.Meta.document, operation.Arguments(**variables).dict(by_alias=True)
    ):
        yield operation(**event.data)
