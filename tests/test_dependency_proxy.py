"""A parameter annotated with a declared protocol receives a proxy made for its task.

The proxy is handed the ``Task`` the action runs for and the agent running it,
the same way an injected client is; nothing is looked up from context. Its
calls leave over the agent's socket as children of that task, and the object it
holds is the one a ``task: Task`` parameter receives.
"""

from typing import Any, Protocol
from collections.abc import AsyncGenerator

import pytest

from rekuest.actors.dependency import AgentDependencyProxy
from rekuest.actors.helper import AssignmentHelper
from rekuest.agents.base import BaseAgent
from rekuest.agents.caller import CallerTaskEvent
from rekuest.api.schema import TaskEventKind
from rekuest.task import Task

from .agent_helpers import run_assignment
from .test_service_client_injection import assign, build_agent


class Lab(Protocol):
    """A lab another app runs."""

    def measure(self, x: int) -> int:
        """Measure."""
        ...

    async def ameasure(self, x: int) -> int:
        """Measure, async."""
        ...


class RecordingCallerPostman:
    """Stands in for the agent's socket postman: records each call, answers ``2 * x``."""

    connected = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def aassign(self, **kwargs: Any) -> AsyncGenerator[CallerTaskEvent, None]:  # noqa: ANN401
        self.calls.append(kwargs)
        yield CallerTaskEvent(
            kind=TaskEventKind.YIELD, returns={"return0": kwargs["args"]["x"] * 2}
        )
        yield CallerTaskEvent(kind=TaskEventKind.COMPLETED)


def agent_with(*bodies: Any) -> tuple[BaseAgent, RecordingCallerPostman]:  # noqa: ANN401
    """An agent whose app declares ``Lab`` and registers ``bodies``, calling out
    through a recording postman."""
    agent = build_agent(None)
    agent.app_registry.declare(app="lab")(Lab)
    for body in bodies:
        agent.app_registry.register(body)
    agent.collect_from_registry()
    postman = RecordingCallerPostman()
    agent._caller_postman = postman  # type: ignore[assignment]
    return agent, postman


def assert_called(postman: RecordingCallerPostman, method: str, parent: str) -> None:
    (call,) = postman.calls
    assert str(call["dependency"]) == "lab"
    assert call["method"] == method
    assert str(call["parent"]) == parent
    assert call["args"] == {"x": 3}


@pytest.mark.asyncio
async def test_a_sync_body_calls_its_dependency_from_a_worker_thread() -> None:
    def use_lab(lab: Lab, x: int) -> int:
        """Sync, in a worker thread."""
        return lab.measure(x)

    agent, postman = agent_with(use_lab)

    assert await run_assignment(agent, assign("use_lab", x=3)) == {"return0": 6}
    assert_called(postman, "measure", "task-use_lab-None")


@pytest.mark.asyncio
async def test_an_async_body_awaits_a_declared_async_method() -> None:
    async def use_lab_async(lab: Lab, x: int) -> int:
        """Async: ``ameasure`` is declared async, so calling it is awaitable."""
        return await lab.ameasure(x)

    agent, postman = agent_with(use_lab_async)

    assert await run_assignment(agent, assign("use_lab_async", x=3)) == {"return0": 6}
    assert_called(postman, "ameasure", "task-use_lab_async-None")


@pytest.mark.asyncio
async def test_an_async_body_can_await_a_sync_method_through_acall() -> None:
    async def use_lab_acall(lab: Lab, x: int) -> int:
        """Async, calling a sync-declared method explicitly."""
        return await lab.measure.acall(x)

    agent, postman = agent_with(use_lab_acall)

    assert await run_assignment(agent, assign("use_lab_acall", x=3)) == {"return0": 6}
    assert_called(postman, "measure", "task-use_lab_acall-None")


@pytest.mark.asyncio
async def test_the_proxy_holds_the_same_task_the_body_receives() -> None:
    seen: list[tuple[Task, Task]] = []

    def same(lab: Lab, task: Task, x: int) -> int:
        """Records what the proxy was made for beside the injected task."""
        seen.append((lab.measure.task, task))
        return lab.measure(x)

    agent, _ = agent_with(same)

    await run_assignment(agent, assign("same", x=3))
    (pair,) = seen
    assert pair[0] is pair[1]
    assert pair[0].id == "task-same-None"


@pytest.mark.asyncio
async def test_a_name_that_is_not_a_declared_action_is_an_attribute_error() -> None:
    def use_lab(lab: Lab, x: int) -> int:
        """Uses the lab."""
        return lab.measure(x)

    agent, _ = agent_with(use_lab)
    message = assign("use_lab", x=3)
    actor = await agent.aspawn_actor_from_assign(message)
    task = Task(AssignmentHelper(assignment=message, actor=actor))

    proxies = await actor.aget_dependency_locals(task)  # type: ignore[attr-defined]
    lab = proxies["lab"]

    with pytest.raises(AttributeError, match="measure"):
        lab.nope
    assert not hasattr(lab, "nope")
    assert hasattr(lab, "measure")
    assert dir(lab) == ["ameasure", "measure"]
    assert lab.measure.agent is agent
    assert lab.measure.structure_registry is actor.structure_registry  # type: ignore[attr-defined]


def test_a_dependency_cannot_be_bound_to_a_task_with_no_assignment() -> None:
    agent, _ = agent_with()
    protocol = agent.app_registry.structure_registry.protocol_for(Lab)

    with pytest.raises(ValueError, match="assignment"):
        AgentDependencyProxy(
            "lab",
            protocol,
            task=Task.local(),
            agent=agent,
            structure_registry=agent.app_registry.structure_registry,
        )
