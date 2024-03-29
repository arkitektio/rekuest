from rath.links.base import ContinuationLink
from rath.operation import GraphQLResult, Operation
from typing import AsyncIterator, Awaitable, Callable, Optional
from rekuest.actors.vars import (
    current_assignment,
    NotWithinAnAssignationError,
)


class SetAssignationLink(ContinuationLink):
    header_name: str = "x-assignation-id"

    async def aconnect(self):
        pass

    async def aexecute(
        self, operation: Operation, **kwargs
    ) -> AsyncIterator[GraphQLResult]:
        try:
            assignment = current_assignment.get()
            operation.context.headers[self.header_name] = assignment.assignation
        except LookupError as e:
            pass

        async for result in self.next.aexecute(operation, **kwargs):
            yield result

    class Config:
        underscore_attrs_are_private = True
        arbitary_types_allowed = True
