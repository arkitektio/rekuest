"""What a real server records from a numbering agent (the agent-report contract).

The unit tests prove the frames the agent sends (``tests/fixtures/agent_wire.json``); these
read back what the server made of them over GraphQL:

- every report of a task carries its session position and task step, and the steps of a
  task are distinct and increasing;
- ``task.now()`` / ``task.random()`` / ``task.sleep()`` land as EFFECT events at their steps;
- a child call is stored with the parent's step as ``parentStep``, and the caller's own
  reference is left alone.
"""

import asyncio
import uuid
from typing import Protocol

import pytest
from dokker import Deployment

from arkitekt_runtime.task import Task

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest

TASKS = """
query Tasks {
  tasks {
    id
    reference
    parentStep
    parent { id }
  }
}
"""

HISTORY = """
query History($id: ID!) {
  task(id: $id) {
    id
    latestEventKind
    events(ordering: [{createdAt: ASC}]) {
      kind
      step
      agentPos
      effect
      value
    }
  }
}
"""


async def _tasks(app) -> list[dict]:  # noqa: ANN001
    return (await app.rath.aquery(TASKS, {})).data["tasks"]


async def _task_by_reference(app, reference: str) -> dict:  # noqa: ANN001
    for task in await _tasks(app):
        if task["reference"] == reference:
            return task
    raise AssertionError(f"no task with reference {reference!r}")


async def _history(app, task_id: str) -> dict:  # noqa: ANN001
    return (await app.rath.aquery(HISTORY, {"id": task_id})).data["task"]


async def _stop(*loops: asyncio.Task) -> None:
    for loop in loops:
        loop.cancel()
        try:
            await loop
        except asyncio.CancelledError:
            pass


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_effects_are_recorded_at_their_steps(deployment: Deployment) -> None:
    app = build_fresh_rekuest(deployment, token="standalone_token")

    def stamped(task: Task) -> str:
        """Take the clock, randomness and a short sleep, each through the task."""
        task.log("before")
        now = task.now()
        drawn = task.random(4)
        task.sleep(0.01)
        return f"{now}:{drawn}"

    app.register(stamped)

    async with app as app:
        await app.aconnect(timeout=CONNECT_TIMEOUT)
        loop = asyncio.create_task(app.aloop())

        reference = f"effects-{uuid.uuid4().hex[:8]}"
        impl = await app.amy_implementation_at("stamped")
        result = await app.acall(impl, reference=reference)
        now, drawn = result.split(":")

        task = await _task_by_reference(app, reference)
        history = await _history(app, task["id"])
        await _stop(loop)

    reported = [e for e in history["events"] if e["agentPos"] is not None]
    assert reported, "the agent's reports carry their session position"
    steps = [e["step"] for e in reported]
    assert all(s is not None for s in steps), history["events"]
    assert steps == sorted(steps) and len(set(steps)) == len(steps), steps

    effects = [
        (e["effect"], e["value"]) for e in history["events"] if e["kind"] == "EFFECT"
    ]
    assert [kind for kind, _ in effects] == ["NOW", "RANDOM", "SLEEP"], history[
        "events"
    ]
    assert effects[0][1] == pytest.approx(float(now))
    assert effects[1][1] == drawn
    assert effects[2][1] >= effects[0][1]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_child_call_is_stored_with_its_parents_step(
    deployment: Deployment,
) -> None:
    # The atest_token client is the "atest" app the workflow depends on.
    provider = build_fresh_rekuest(deployment, token="atest_token")
    workflow = build_fresh_rekuest(deployment, token="workflow_token")

    def double(x: int) -> int:
        """Double a number."""
        return 2 * x

    provider.register(double)

    @workflow.declare(app="atest", auto_resolvable=True, min=1)
    class Doubler(Protocol):
        def double(self, x: int) -> int:
            """Double a number."""
            ...

    def twice(atest: Doubler, x: int) -> int:
        """Double a number twice, on the provider."""
        return atest.double(atest.double(x))

    workflow.register_workflow(twice)

    async with provider as provider, workflow as workflow:
        await provider.aconnect(timeout=CONNECT_TIMEOUT)
        provider_loop = asyncio.create_task(provider.aloop())
        await workflow.aconnect(timeout=CONNECT_TIMEOUT)
        workflow_loop = asyncio.create_task(workflow.aloop())

        reference = f"twice-{uuid.uuid4().hex[:8]}"
        impl = await workflow.amy_implementation_at("twice")
        assert await workflow.acall(impl, x=3, reference=reference) == 12

        parent = await _task_by_reference(workflow, reference)
        # The child runs on the provider; either client may be the one allowed to list it.
        seen = {t["id"]: t for t in [*await _tasks(workflow), *await _tasks(provider)]}
        children = [
            t for t in seen.values() if (t["parent"] or {}).get("id") == parent["id"]
        ]
        await _stop(provider_loop, workflow_loop)

    assert len(children) == 2, children
    steps = sorted(c["parentStep"] for c in children)
    assert None not in steps and steps[0] < steps[1], children
    # The reference is the caller's own, never derived from the step.
    assert all(
        c["reference"] != f"{parent['id']}:{c['parentStep']}" for c in children
    ), children
