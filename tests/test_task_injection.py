"""A parameter annotated `Task` receives the task it runs for.

Everything the module-level helpers reach through context variables -- logging,
progress, pause points, child calls, per-task client views -- is a method of an
object the action was handed.
"""

import asyncio
from typing import Any, Optional

import pytest

from rekuest import messages
from rekuest.api.schema import PortKind
from rekuest.definition.define import prepare_definition
from rekuest.structures.registry import StructureRegistry
from rekuest.api.schema import Action
from rekuest.task import Task

from .agent_helpers import run_assignment
from .service_helpers import with_client
from .test_service_client_injection import FakeApp, FakeClient, assign, build_agent


ACTION = Action.model_construct(id="action-1")


def test_task_parameter_is_not_a_port() -> None:
    def takes_task(x: int, task: Task, maybe: Optional[Task] = None) -> int:
        """Takes its task."""
        return x

    definition = prepare_definition(takes_task, structure_registry=StructureRegistry())
    assert [port.key for port in definition.args] == ["x"]
    assert definition.args[0].kind == PortKind.INT


@pytest.mark.asyncio
async def test_reports_through_the_injected_task_in_loop_and_thread() -> None:
    agent_a = build_agent(FakeApp("A", FakeClient("A")))
    agent_b = build_agent(FakeApp("B", FakeClient("B")))

    async def in_loop(task: Task) -> str:
        """Async."""
        await task.alog("hello from loop")
        await task.aprogress(40, "loop")
        return task.id

    def in_thread(task: Task) -> str:
        """Sync, in a worker thread: no context is needed to reach the task."""
        task.log("hello from thread")
        task.progress(60, "thread")
        task.pausepoint()
        return task.id

    agent_a.app_registry.register(in_loop)
    agent_a.collect_from_registry()
    agent_b.app_registry.register(in_thread)
    agent_b.collect_from_registry()

    returns = await asyncio.gather(
        run_assignment(agent_a, assign("in_loop")),
        run_assignment(agent_b, assign("in_thread")),
    )
    assert returns == [{"return0": "task-in_loop-None"}, {"return0": "task-in_thread-None"}]

    for agent, text, pct in ((agent_a, "loop", 40), (agent_b, "thread", 60)):
        logs = agent.transport.of_type(messages.Log)  # type: ignore[attr-defined]
        progress = agent.transport.of_type(messages.Progress)  # type: ignore[attr-defined]
        assert any(text in log.message for log in logs)
        assert (pct, text) in [(p.progress, p.message) for p in progress]


@pytest.mark.asyncio
async def test_an_injected_rekuest_client_parents_its_calls_to_the_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Child calls belong to the rekuest *service*, viewed for this task."""
    from rekuest.rekuest import Rekuest

    app = FakeApp("A")
    agent = build_agent(app)
    # `rekuest: Rekuest` is a client of this registry because a service on it returns one.
    with_client(agent.app_registry, Rekuest, "rekuest")
    rekuest = Rekuest.model_construct(
        postman="graphql-postman",
        structure_registry=agent.app_registry.structure_registry,
        parent=None,
    )
    app.services["rekuest"] = rekuest
    seen: dict[str, Any] = {}

    async def fake_acall(target: Any, *args: Any, **kwargs: Any) -> str:  # noqa: ANN401
        seen.update(kwargs, target=target, args=args)
        return "child-result"

    monkeypatch.setattr("rekuest.remote.acall", fake_acall)

    async def parent(x: int, rekuest: Rekuest, task: Task) -> str:
        """Calls a child through the injected client."""
        await task.aprogress(10)
        return await rekuest.acall(ACTION, x, reference="r")

    agent.app_registry.register(parent)
    agent.collect_from_registry()

    assert await run_assignment(agent, assign("parent", x=1)) == {"return0": "child-result"}
    assert seen["parent"].task == "task-parent-None"
    assert seen["postman"] is agent.caller_postman
    assert seen["structure_registry"] is agent.app_registry.structure_registry
    assert (seen["target"], seen["args"], seen["reference"]) == (ACTION, (1,), "r")
    assert rekuest.parent is None, "the shared client is never changed"


@pytest.mark.asyncio
async def test_outside_a_task_the_rekuest_client_calls_over_its_own_postman(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rekuest.rekuest import Rekuest

    agent = build_agent(FakeApp("A"))
    rekuest = Rekuest.model_construct(
        postman="graphql-postman",
        structure_registry=agent.app_registry.structure_registry,
        parent=None,
    )
    seen: dict[str, Any] = {}

    async def fake_acall(target: Any, *args: Any, **kwargs: Any) -> str:  # noqa: ANN401
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr("rekuest.remote.acall", fake_acall)
    assert await rekuest.acall(ACTION) == "ok"
    assert seen["postman"] == "graphql-postman" and "parent" not in seen

    # A local task runs under no agent: its view is the client itself, calling roots.
    assert rekuest.for_task(Task.local()) is rekuest


@pytest.mark.asyncio
async def test_a_task_knows_the_agent_running_it() -> None:
    agent = build_agent(FakeApp("A"))
    seen: dict[str, Any] = {}

    def who(task: Task) -> str:
        """Records its agent."""
        seen["agent"] = task.agent
        return task.id

    agent.app_registry.register(who)
    agent.collect_from_registry()

    await run_assignment(agent, assign("who"))
    assert seen["agent"] is agent
    assert Task.local().agent is None


def test_actor_kwargs_carry_every_derived_field() -> None:
    """A new ImplementationDetails field must reach actors built by any actifier."""
    import dataclasses

    from rekuest.actors.actify import derive_implementation_details
    from rekuest.actors.types import ImplementationDetails, RegisterConfig

    def f(x: int, task: Task) -> int:
        """F."""
        return x

    details = derive_implementation_details(f, RegisterConfig())
    # Not actor state: they describe the registration, not the running actor.
    registration_only = {"tracks", "manipulates"}
    fields = {f.name for f in dataclasses.fields(ImplementationDetails)}
    assert set(details.actor_kwargs()) == fields - registration_only
    assert details.actor_kwargs()["injected_variables"].task_variables == ["task"]
