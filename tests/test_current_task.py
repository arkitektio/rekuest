"""The task an action runs for is ambient while its body runs.

The clients an action is handed are shared, not per-task copies; what makes a
request attributable is `rath.task.current_task`, which the actor sets around the
body. These pin that it is right for every strategy, that concurrent assignments
do not see each other's, and that it never outlives the assignment.

The limits are pinned too, in `test_what_does_not_carry`: a task does not reach a
thread the action starts itself, and does reach one that outlives it. Both are
properties of `contextvars`, not bugs to fix here -- written down so the next
reader meets them as decisions.
"""

import asyncio
import threading
import time
from collections.abc import AsyncGenerator, Generator
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from koil import unkoil
from rath.task import current_task, token_of

from rekuest import messages
from rekuest.agents.base import BaseAgent
from rekuest.app import AppRegistry

from .agent_helpers import run_assignment
from .memory_transport import MemoryAgentTransport


def build_agent(name: str = "agent") -> BaseAgent:
    return BaseAgent(
        name=name,
        transport=MemoryAgentTransport(),
        app_registry=AppRegistry(),
    )


def assign(interface: str, token: str | None = None, **args: Any) -> messages.Assign:
    return messages.Assign(
        task=f"task-{interface}-{token}",
        interface=interface,
        args=args,
        implementation="impl-1",
        action="action-1",
        reference="ref-1",
        user="user-1",
        org="org-1",
        token=token,
    )


async def run_one(func: Any, token: str | None = None) -> dict[str, Any]:  # noqa: ANN401
    """Register ``func`` on a fresh agent and drive one assignment through it."""
    agent = build_agent()
    agent.app_registry.register(func)
    agent.collect_from_registry()
    return await run_assignment(agent, assign(func.__name__, token=token))


def ambient_token() -> str | None:
    task = current_task.get()
    return getattr(task, "token", None)


# --------------------------------------------------------------------------- #
# Every strategy
# --------------------------------------------------------------------------- #


async def async_action() -> str:
    """An async action."""
    return str(ambient_token())


def threaded_action() -> str:
    """A sync action, which runs in a worker thread."""
    time.sleep(0.01)
    return str(ambient_token())


async def async_gen_action() -> AsyncGenerator[str, None]:
    """An async generator action."""
    for _ in range(3):
        await asyncio.sleep(0)
        yield str(ambient_token())


def threaded_gen_action() -> Generator[str, None, None]:
    """A sync generator action, whose steps run in worker threads."""
    for _ in range(3):
        time.sleep(0.01)
        yield str(ambient_token())


@pytest.mark.asyncio
@pytest.mark.parametrize("func", [async_action, threaded_action])
async def test_a_function_action_sees_its_task(func: Any) -> None:  # noqa: ANN401
    """Both in the loop and across the thread hop."""
    assert await run_one(func, token="t1") == {"return0": "t1"}


@pytest.mark.asyncio
@pytest.mark.parametrize("func", [async_gen_action, threaded_gen_action])
async def test_a_generator_action_sees_its_task_on_every_yield(func: Any) -> None:  # noqa: ANN401
    """Not just the first: a threaded generator is re-seeded from the caller each
    step, on a possibly different pooled thread."""
    agent = build_agent()
    agent.app_registry.register(func)
    agent.collect_from_registry()
    transport: MemoryAgentTransport = agent.transport  # type: ignore[assignment]

    # `run_assignment` returns at the first Yield; wait for the whole stream.
    await run_assignment(agent, assign(func.__name__, token="t1"))

    async def until_completed() -> None:
        while not transport.of_type(messages.Completed):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(until_completed(), timeout=5.0)

    seen = [y.returns["return0"] for y in transport.of_type(messages.Yield)]
    assert seen == ["t1", "t1", "t1"], "the task must hold across yields"


# --------------------------------------------------------------------------- #
# It belongs to this assignment and no other
# --------------------------------------------------------------------------- #


async def slow_reporter() -> str:
    """Reports its task after giving the others a chance to interleave."""
    await asyncio.sleep(0.01)
    return str(ambient_token())


@pytest.mark.asyncio
async def test_concurrent_assignments_do_not_see_each_others_task() -> None:
    agents = [build_agent(f"a{i}") for i in range(3)]
    for agent in agents:
        agent.app_registry.register(slow_reporter)
        agent.collect_from_registry()

    results = await asyncio.gather(
        run_assignment(agents[0], assign("slow_reporter", token="t1")),
        run_assignment(agents[1], assign("slow_reporter", token="t2")),
        run_assignment(agents[2], assign("slow_reporter")),
    )

    assert results == [{"return0": "t1"}, {"return0": "t2"}, {"return0": "None"}]


@pytest.mark.asyncio
async def test_nothing_is_current_outside_an_assignment() -> None:
    """Before, and after -- including after one that failed."""
    assert current_task.get() is None
    await run_one(async_action, token="t1")
    assert current_task.get() is None, "the task outlived its assignment"


async def exploding_action() -> str:
    """Raises, so the scope has to unwind through the exception."""
    raise ValueError("boom")


@pytest.mark.asyncio
async def test_a_failed_assignment_still_unwinds_the_task() -> None:
    """`task_scope` unwinds through the exception, not only on the happy path."""
    agent = build_agent()
    agent.app_registry.register(exploding_action)
    agent.collect_from_registry()
    transport: MemoryAgentTransport = agent.transport  # type: ignore[assignment]

    transport.feed(assign("exploding_action", token="t1"))

    async def drive_one() -> None:
        # `areceive()` blocks for the next message, so take exactly one.
        async for message in agent.transport.areceive():
            await agent.process(message)
            return

    await asyncio.wait_for(drive_one(), timeout=2.0)

    async def until_failed() -> None:
        while not (
            transport.of_type(messages.Critical) or transport.of_type(messages.Failed)
        ):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(until_failed(), timeout=5.0)
    assert current_task.get() is None, "the task outlived a failed assignment"


# --------------------------------------------------------------------------- #
# What does not carry, pinned as a decision
# --------------------------------------------------------------------------- #


def spawns_its_own_threads() -> str:
    """A thread the action starts itself inherits nothing; a carried context does."""
    import contextvars

    seen: dict[str, str | None] = {}

    bare = threading.Thread(target=lambda: seen.__setitem__("bare", ambient_token()))
    bare.start()
    bare.join()

    with ThreadPoolExecutor(1) as pool:
        seen["pool"] = pool.submit(ambient_token).result()

    ctx = contextvars.copy_context()
    carried = threading.Thread(
        target=lambda: ctx.run(lambda: seen.__setitem__("carried", ambient_token()))
    )
    carried.start()
    carried.join()

    return f"{seen['bare']},{seen['pool']},{seen['carried']}"


@pytest.mark.asyncio
async def test_what_does_not_carry() -> None:
    """A raw thread start drops the task. This is the price of an ambient task.

    It is unattributed rather than misattributed, which is the milder failure, and
    the workaround -- carry the context, or pass the task explicitly -- works.
    """
    result = await run_one(spawns_its_own_threads, token="t1")
    assert result == {"return0": "None,None,t1"}


# --------------------------------------------------------------------------- #
# End to end: the token actually reaches the request
# --------------------------------------------------------------------------- #


class RecordingRath:
    """As much of a rath as a generated client uses, remembering the headers."""

    middlewares: list[Any] = []

    def __init__(self) -> None:
        self.headers: list[Any] = []

    async def aquery(
        self, document: str, variables: dict[str, Any], headers: Any = None  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        self.headers.append(headers)
        return SimpleNamespace(data={"ok": True})


class StampingClient:
    """A client shaped like the generated ones: it stamps the task per call."""

    TASK_HEADER = "Rekuest-Task"

    def __init__(self) -> None:
        self.rath = RecordingRath()

    async def acall_something(self, task: Any = None) -> str:  # noqa: ANN401
        token = token_of(task)
        headers = {self.TASK_HEADER: token} if token else None
        await self.rath.aquery("query { ok }", {}, headers=headers)
        return str(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "func_name", ["e2e_async", "e2e_threaded", "e2e_async_gen", "e2e_threaded_gen"]
)
async def test_the_token_reaches_the_request_for_every_strategy(func_name: str) -> None:
    """The gate: a real assignment, a real client call, the exact header on it.

    Every unit test above checks what `current_task` says. This checks what goes
    out -- the only thing the server sees, and the only thing a wrong answer here
    would show up in.
    """
    client = StampingClient()

    async def e2e_async() -> str:
        """An async action."""
        return await client.acall_something()

    def e2e_threaded() -> str:
        """A sync action."""
        return unkoil(client.acall_something)

    async def e2e_async_gen() -> AsyncGenerator[str, None]:
        """An async generator action."""
        yield await client.acall_something()

    def e2e_threaded_gen() -> Generator[str, None, None]:
        """A sync generator action."""
        yield unkoil(client.acall_something)

    func = {
        "e2e_async": e2e_async,
        "e2e_threaded": e2e_threaded,
        "e2e_async_gen": e2e_async_gen,
        "e2e_threaded_gen": e2e_threaded_gen,
    }[func_name]

    agent = build_agent()
    agent.app_registry.register(func)
    agent.collect_from_registry()
    await run_assignment(agent, assign(func_name, token="tok-1"))

    assert client.rath.headers == [{"Rekuest-Task": "tok-1"}], (
        "the task must reach the outgoing request, not just the contextvar"
    )
