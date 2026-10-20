"""A child an agent asks for waits for a person, unless the run is in auto-mode.

The workflow app calls the provider's action through a declared dependency. Under ASK
(the default for a root) the server creates the child HELD and dispatches nothing: the
root shows what waits, a person decides from their own client, and the workflow either
gets the child's result or a ``DeniedError``. The agent's own client may never decide.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import pytest
from dokker import Deployment
from rath.operation import GraphQLException

from arkitekt_spec.declare.errors import DeniedError

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest

TASKS = "query { tasks { id reference latestEventKind policy } }"
HISTORY = """
query History($id: ID!) {
  task(id: $id) { latestEventKind policy events(ordering: [{createdAt: ASC}]) { kind message value } }
}
"""
DECIDE = """
mutation Decide($task: ID!, $approved: Boolean!, $answer: String) {
  decide(input: {task: $task, approved: $approved, answer: $answer}) { id latestEventKind }
}
"""
TIMEOUT = 30


async def _eventually(probe: Callable[[], Awaitable[Any]], what: str) -> Any:  # noqa: ANN401
    deadline = time.monotonic() + TIMEOUT
    while True:
        value = await probe()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.2)


async def _query(app: Any, query: str, variables: dict | None = None) -> dict:  # noqa: ANN401
    return (await app.rath.aquery(query, variables or {})).data


def _apps(deployment: Deployment) -> tuple[Any, Any, Any]:  # noqa: ANN401
    provider = build_fresh_rekuest(deployment, token="atest_token")

    def do_stuff(printer: str) -> str:
        """Stitch a list of images."""
        return "stitched-" + printer

    provider.register(do_stuff)

    workflow_app = build_fresh_rekuest(deployment, token="workflow_token")

    @workflow_app.declare(app="atest", auto_resolvable=True, min=1)
    class ATestLike(Protocol):
        def do_stuff(self, printer: str) -> str:
            """Stitch a list of images."""
            ...

    def asks(atest: ATestLike) -> str:
        """Ask for the provider's action; say what came of it."""
        try:
            return atest.do_stuff("printer")
        except DeniedError as denied:
            return f"denied:{denied.answer}"

    workflow_app.register_workflow(asks)
    decider = build_fresh_rekuest(deployment, token="workflow_decider_token")
    return provider, workflow_app, decider


async def _held_child(app: Any, reference: str) -> tuple[str, dict]:  # noqa: ANN401
    """The root's id, once its HELD event names the child that waits."""

    async def root() -> str | None:
        tasks = (await _query(app, TASKS))["tasks"]
        return next((t["id"] for t in tasks if t["reference"] == reference), None)

    root_id = await _eventually(root, "the root to exist")

    async def held() -> dict | None:
        events = (await _query(app, HISTORY, {"id": root_id}))["task"]["events"]
        return next((e["value"] for e in events if e["kind"] == "HELD" and (e["value"] or {}).get("held")), None)

    return root_id, await _eventually(held, "the root to show a held child")


async def _run(deployment: Deployment, decide: Callable[[Any, Any, str, dict], Awaitable[None]]) -> str:  # noqa: ANN401
    provider, workflow_app, decider = _apps(deployment)
    async with provider as provider, workflow_app as workflow_app, decider as decider:
        await provider.aconnect(timeout=CONNECT_TIMEOUT)
        loops = [asyncio.create_task(provider.aloop())]
        await workflow_app.aconnect(timeout=CONNECT_TIMEOUT)
        loops.append(asyncio.create_task(workflow_app.aloop()))
        try:
            impl = await workflow_app.amy_implementation_at("asks")
            reference = f"asks-{uuid.uuid4().hex[:8]}"
            pending = asyncio.create_task(workflow_app.acall(impl, reference=reference))

            root_id, held = await _held_child(workflow_app, reference)
            child = (await _query(workflow_app, HISTORY, {"id": held["held"]}))["task"]
            assert child["latestEventKind"] == "HELD", "the child waits, nothing was dispatched"
            assert child["policy"] == "ASK"
            assert held["kind"] == "assign" and held["action"]["name"]
            await asyncio.sleep(0.5)
            assert not pending.done(), "the workflow waits while its child is held"

            await decide(workflow_app, decider, held["held"], held)
            return await asyncio.wait_for(pending, TIMEOUT)
        finally:
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_held_child_runs_once_approved_and_never_by_its_agent(deployment: Deployment) -> None:
    async def approve(app: Any, decider: Any, child: str, held: dict) -> None:  # noqa: ANN401
        # The workflow's own client asked for it, so it may not decide it.
        with pytest.raises(GraphQLException):
            await _query(app, DECIDE, {"task": child, "approved": True})
        await _query(decider, DECIDE, {"task": child, "approved": True, "answer": "go ahead"})

    assert await _run(deployment, approve) == "stitched-printer"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_denied_child_never_runs_and_its_caller_gets_denied_error(deployment: Deployment) -> None:
    async def deny(app: Any, decider: Any, child: str, held: dict) -> None:  # noqa: ANN401
        await _query(decider, DECIDE, {"task": child, "approved": False, "answer": "not now"})
        history = (await _query(app, HISTORY, {"id": child}))["task"]
        assert history["latestEventKind"] == "DENIED"
        assert [e["kind"] for e in history["events"] if e["kind"] in ("HELD", "DECIDED", "DENIED")] == [
            "HELD",
            "DECIDED",
            "DENIED",
        ]

    assert await _run(deployment, deny) == "denied:not now"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_auto_root_runs_its_children_without_asking(deployment: Deployment) -> None:
    from arkitekt_runtime.types import TaskPolicy

    provider, workflow_app, _ = _apps(deployment)
    async with provider as provider, workflow_app as workflow_app:
        await provider.aconnect(timeout=CONNECT_TIMEOUT)
        loops = [asyncio.create_task(provider.aloop())]
        await workflow_app.aconnect(timeout=CONNECT_TIMEOUT)
        loops.append(asyncio.create_task(workflow_app.aloop()))
        try:
            impl = await workflow_app.amy_implementation_at("asks")
            reference = f"auto-{uuid.uuid4().hex[:8]}"
            answer = await asyncio.wait_for(
                workflow_app.acall(impl, reference=reference, policy=TaskPolicy.AUTO), TIMEOUT
            )
            assert answer == "stitched-printer"
            tasks = (await _query(workflow_app, TASKS))["tasks"]
            assert next(t for t in tasks if t["reference"] == reference)["policy"] == "AUTO"
        finally:
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_the_prompt_the_agent_writes_is_shown_on_the_held_child(deployment: Deployment) -> None:
    from arkitekt_spec.declare.task import AssignPrompt, Task

    provider, workflow_app, decider = _apps(deployment)
    target: list[Any] = []

    async def explains(task: Task) -> str:
        """Ask for the provider's action, saying why."""
        return await task.acall(
            target[0], printer="p", prompt=AssignPrompt(title="Stitch it", description="The room asked for a poster.")
        )

    workflow_app.register_workflow(explains)
    async with provider as provider, workflow_app as workflow_app, decider as decider:
        await provider.aconnect(timeout=CONNECT_TIMEOUT)
        loops = [asyncio.create_task(provider.aloop())]
        await workflow_app.aconnect(timeout=CONNECT_TIMEOUT)
        loops.append(asyncio.create_task(workflow_app.aloop()))
        try:
            target.append(await provider.amy_implementation_at("do_stuff"))
            impl = await workflow_app.amy_implementation_at("explains")
            reference = f"explains-{uuid.uuid4().hex[:8]}"
            pending = asyncio.create_task(workflow_app.acall(impl, reference=reference))

            _, held = await _held_child(workflow_app, reference)
            assert held["prompt"] == {"title": "Stitch it", "description": "The room asked for a poster."}
            child = (await _query(workflow_app, HISTORY, {"id": held["held"]}))["task"]
            [event] = [e for e in child["events"] if e["kind"] == "HELD"]
            assert event["message"] == "Stitch it"

            await _query(decider, DECIDE, {"task": held["held"], "approved": True})
            assert await asyncio.wait_for(pending, TIMEOUT) == "stitched-p"
        finally:
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_task_holding_itself_gets_the_decision_and_auto_mode_decides_at_once(deployment: Deployment) -> None:
    from arkitekt_runtime.types import TaskPolicy
    from arkitekt_spec.declare.task import AssignPrompt, Task

    _, workflow_app, decider = _apps(deployment)

    def overwrites(task: Task) -> str:
        """Ask before overwriting."""
        decision = task.hold("May I overwrite?", prompt=AssignPrompt(title="Overwrite the plate"))
        return f"{decision.approved}:{decision.answer}:{decision.auto}"

    workflow_app.register_workflow(overwrites)
    async with workflow_app as workflow_app, decider as decider:
        await workflow_app.aconnect(timeout=CONNECT_TIMEOUT)
        loop = asyncio.create_task(workflow_app.aloop())
        try:
            impl = await workflow_app.amy_implementation_at("overwrites")
            reference = f"self-{uuid.uuid4().hex[:8]}"
            pending = asyncio.create_task(workflow_app.acall(impl, reference=reference))

            async def held() -> str | None:
                tasks = (await _query(workflow_app, TASKS))["tasks"]
                task = next((t for t in tasks if t["reference"] == reference), None)
                return task["id"] if task and task["latestEventKind"] == "HELD" else None

            task_id = await _eventually(held, "the task to hold itself")
            events = (await _query(workflow_app, HISTORY, {"id": task_id}))["task"]["events"]
            [hold] = [e for e in events if e["kind"] == "HELD"]
            assert hold["value"]["kind"] == "self" and hold["value"]["prompt"]["title"] == "Overwrite the plate"

            with pytest.raises(GraphQLException):  # the agent's own client never decides
                await _query(workflow_app, DECIDE, {"task": task_id, "approved": True})
            await _query(decider, DECIDE, {"task": task_id, "approved": False, "answer": "keep it"})
            assert await asyncio.wait_for(pending, TIMEOUT) == "False:keep it:False"

            auto = await asyncio.wait_for(workflow_app.acall(impl, policy=TaskPolicy.AUTO), TIMEOUT)
            assert auto == "True:None:True"
        finally:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)
