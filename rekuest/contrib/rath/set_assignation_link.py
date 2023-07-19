from rath.links.base import ContinuationLink
from rath.operation import GraphQLResult, Operation
from typing import AsyncIterator, Awaitable, Callable, Optional
from rekuest.actors.vars import (
    get_current_assignation_helper,
    NotWithinAnAssignationError,
)


class SetAssignationLink(ContinuationLink):
    header_name: str = "ASSIGNATION_ID"

    async def aconnect(self):
        pass

    async def aexecute(
        self, operation: Operation, **kwargs
    ) -> AsyncIterator[GraphQLResult]:
        try:
            asshelper = get_current_assignation_helper()
            operation.context.headers[
                self.header_name
            ] = asshelper.assignment.assignation
        except NotWithinAnAssignationError:
            pass

        async for result in self.next.aexecute(operation, **kwargs):
            yield result

    class Config:
        underscore_attrs_are_private = True
        arbitary_types_allowed = True
