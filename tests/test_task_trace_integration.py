"""A task, and the child its agent asks for, stay in the trace the task was assigned in.

Nobody records here (no collector is configured anywhere): the trace is only passed on. The
request that assigns the root is made inside a trace; the server hands it to the workflow's
agent with the assignment, the agent's child call says where it was made, and the provider's
agent is handed the same trace with the child.
"""

import asyncio
import secrets
from typing import Protocol

import pytest
from arkitekt_runtime.trace import current_traceparent
from arkitekt_runtime.types import TaskPolicy
from dokker import Deployment
from opentelemetry import context, propagate

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest

TIMEOUT = 30


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_child_is_handed_the_trace_its_root_was_assigned_in(
    deployment: Deployment,
) -> None:
    provider = build_fresh_rekuest(deployment, token="atest_token")

    def where_am_i() -> str:
        """Say which trace this runs in."""
        return current_traceparent() or "none"

    provider.register(where_am_i)
    workflow_app = build_fresh_rekuest(deployment, token="workflow_token")

    @workflow_app.declare(app="atest", auto_resolvable=True, min=1)
    class Traced(Protocol):
        def where_am_i(self) -> str:
            """Say which trace this runs in."""
            ...

    def asks_where(atest: Traced) -> str:
        """Where this runs, and where the child it asked for ran."""
        return f"{current_traceparent() or 'none'} {atest.where_am_i()}"

    workflow_app.register_workflow(asks_where)

    async with provider as provider, workflow_app as workflow_app:
        await provider.aconnect(timeout=CONNECT_TIMEOUT)
        loops = [asyncio.create_task(provider.aloop())]
        await workflow_app.aconnect(timeout=CONNECT_TIMEOUT)
        loops.append(asyncio.create_task(workflow_app.aloop()))
        try:
            impl = await workflow_app.amy_implementation_at("asks_where")
            traceparent = f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"
            entered = context.attach(propagate.extract({"traceparent": traceparent}))
            try:
                answer = await asyncio.wait_for(
                    workflow_app.acall(impl, policy=TaskPolicy.AUTO), TIMEOUT
                )
            finally:
                context.detach(entered)
            assert answer == f"{traceparent} {traceparent}", (
                "the root's agent and the child's agent were both handed the trace, unchanged"
            )

            untraced = await asyncio.wait_for(
                workflow_app.acall(impl, policy=TaskPolicy.AUTO), TIMEOUT
            )
            assert untraced == "none none", "a task assigned in no trace is in none"
        finally:
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)
