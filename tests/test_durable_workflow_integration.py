"""Durable workflows: a workflow whose agent is killed mid-task is resumed, not redone.

A real subprocess agent runs the workflow and is SIGKILLed at a crash point; a successor
takes the agent over under the same name and journal. The server sends the workflow again
with its journal, and the successor's run replays it: recorded values come back the same,
and calls it already made find their children again (and their results) by call key.

Most kills wait until the dying process has nothing unacknowledged left, so the journal
the server resumes from is complete; one kills at once, where the successor completes it
from the dead process's journal on disk.
"""

import asyncio
import os
import sqlite3
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from dokker import Deployment

from arkitekt_spec.declare.errors import AgentLost

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest, build_rekuest_at, rekuest_port
from .durable_workflow import (
    AGENT_NAME,
    CRASH_MARKER_ENV,
    DECLARE,
    declare_handles_a_lost_step,
    declare_hang,
    declare_pipeline_changed,
)

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
WORKER_TIMEOUT = 60
RESUME_TIMEOUT = 30

TASKS = "query { tasks { id reference parentStep callKey parent { id } } }"
HISTORY = """
query History($id: ID!) {
  task(id: $id) {
    latestEventKind
    events(ordering: [{createdAt: ASC}]) { kind step agentPos effect value message }
  }
}
"""
RESUME = "mutation Resume($id: ID!) { resume(input: {task: $id}) { id } }"


async def _eventually(probe: Callable[[], Awaitable[Any]], timeout: float, what: str) -> Any:  # noqa: ANN401
    deadline = time.monotonic() + timeout
    while True:
        value = await probe()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.25)


def _unacked(journal: Path) -> int:
    """Frames the dying process still holds: recorded, not yet acknowledged by the server."""
    if not journal.exists():
        return 0
    with sqlite3.connect(f"file:{journal}?mode=ro", uri=True) as db:
        return db.execute("SELECT COUNT(*) FROM journal").fetchone()[0]


async def _tasks(app: Any) -> list[dict]:  # noqa: ANN401
    return (await app.rath.aquery(TASKS, {})).data["tasks"]


async def _history(app: Any, task_id: str) -> dict:  # noqa: ANN401
    return (await app.rath.aquery(HISTORY, {"id": task_id})).data["task"]


async def _find(app: Any, reference: str) -> dict | None:  # noqa: ANN401
    return next((t for t in await _tasks(app) if t["reference"] == reference), None)


@dataclass
class Run:
    """What a kill-and-take-over left behind."""

    result: Any = None
    error: BaseException | None = None
    events: list[dict] = field(default_factory=list)
    children: list[dict] = field(default_factory=list)
    double_calls: list[int] = field(default_factory=list)

    def effects(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["kind"] == "EFFECT" and e["effect"] == kind]

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.events]


class Worker:
    """The agent process the test kills."""

    def __init__(self, scratch: Path, port: int, serve: str) -> None:
        self.journal, self.marker, self.ready, self.log = (scratch / n for n in ("journal.db", "reached", "ready", "worker.log"))
        self.port, self.serve = port, serve
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        with self.log.open("w") as log:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "tests.durable_worker", cwd=PACKAGE_ROOT,
                env={
                    **os.environ,  # the same PYTHONPATH: local sources
                    "DURABLE_PORT": str(self.port),
                    "DURABLE_JOURNAL": str(self.journal),
                    "DURABLE_READY": str(self.ready),
                    "DURABLE_DECLARE": self.serve,
                    CRASH_MARKER_ENV: str(self.marker),
                },
                stdout=log, stderr=asyncio.subprocess.STDOUT,
            )
        await self.wait_for(lambda: self.ready.exists(), "the worker to connect")

    async def wait_for(self, check: Callable[[], bool], what: str) -> None:
        try:
            await _eventually(lambda: asyncio.sleep(0, check()), WORKER_TIMEOUT, what)
        except AssertionError as e:
            tail = "\n".join(self.log.read_text().splitlines()[-40:])
            raise AssertionError(f"{e}; the worker's log ends:\n{tail}") from None

    async def kill_once_all_is_acknowledged(self) -> None:
        await self.wait_for(lambda: _unacked(self.journal) == 0, "the worker's frames to be acknowledged")
        self.kill()
        assert self.process is not None
        await self.process.wait()

    def kill(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.kill()


async def kill_and_take_over(
    deployment: Deployment,
    scratch: Path,
    *,
    serve: str,
    call: dict[str, Any],
    successor_declares: Callable[[Any], None] | None = None,
    reached: Callable[[Any, str], Awaitable[bool]] | None = None,
    after_take_over: Callable[[Any, str], Awaitable[None]] | None = None,
    wait_for_acks: bool = True,
) -> Run:
    """Call ``serve`` on a worker, kill it where it blocks, and let a successor take over."""
    port = rekuest_port(deployment)
    run = Run()

    def double(x: int) -> int:
        """Double a number."""
        run.double_calls.append(x)
        return 2 * x

    provider = build_fresh_rekuest(deployment, token="atest_token")
    provider.register(double)
    caller = build_fresh_rekuest(deployment, token="durable_token")  # GraphQL only
    worker = Worker(scratch, port, serve)

    async with provider, caller:
        await provider.aconnect(timeout=CONNECT_TIMEOUT)
        provider_loop = asyncio.create_task(provider.aloop())
        try:
            await worker.start()
            impl = await caller.amy_implementation_at(serve)
            reference = f"{serve}-{uuid.uuid4().hex[:8]}"
            pending = asyncio.create_task(caller.acall(impl, reference=reference, **call))
            task_id = (await _eventually(lambda: _find(caller, reference), WORKER_TIMEOUT, "the task to exist"))["id"]
            if reached is None:
                await worker.wait_for(lambda: worker.marker.exists(), "the worker to reach its crash point")
            else:
                await _eventually(lambda: reached(caller, task_id), WORKER_TIMEOUT, "the worker to get there")
            if wait_for_acks:
                await worker.kill_once_all_is_acknowledged()
            else:
                worker.kill()
                assert worker.process is not None
                await worker.process.wait()

            successor = build_rekuest_at(port, "durable_token", name=AGENT_NAME, journal_path=str(worker.journal))
            (successor_declares or DECLARE[serve])(successor)
            async with successor:
                await successor.aconnect(timeout=CONNECT_TIMEOUT)
                loop = asyncio.create_task(successor.aloop())
                try:
                    if after_take_over is not None:
                        await after_take_over(caller, task_id)
                    try:
                        run.result = await asyncio.wait_for(pending, RESUME_TIMEOUT)
                    except (AgentLost, TimeoutError) as e:
                        run.error = e
                    run.events = (await _history(caller, task_id))["events"]
                    seen = {t["id"]: t for t in [*await _tasks(caller), *await _tasks(provider)]}
                    run.children = [t for t in seen.values() if (t["parent"] or {}).get("id") == task_id]
                finally:
                    loop.cancel()
                    await asyncio.gather(loop, return_exceptions=True)
        finally:
            worker.kill()
            provider_loop.cancel()
            await asyncio.gather(provider_loop, return_exceptions=True)
    return run


def _paused(times: int) -> Callable[[Any, str], Awaitable[bool]]:
    async def check(app: Any, task_id: str) -> bool:  # noqa: ANN401
        events = (await _history(app, task_id))["events"]
        return sum(e["kind"] == "PAUSED" for e in events) >= times

    return check


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_workflow_resumes_and_does_not_redo_what_it_finished(deployment: Deployment, tmp_path: Path) -> None:
    run = await kill_and_take_over(deployment, tmp_path, serve="pipeline", call={"x": 3})

    assert run.error is None, (run.error, run.kinds())
    doubled, now, drawn = run.result.split(":")
    # Recorded values come back the same...
    assert float(now) == pytest.approx(run.effects("NOW")[0]["value"])
    assert drawn == run.effects("RANDOM")[0]["value"]
    # ...and each only once: the resumed run replayed them, it did not take new ones.
    assert (len(run.effects("NOW")), len(run.effects("RANDOM"))) == (1, 1)
    # The child finished before the kill: found again, its result reused, not run again.
    assert doubled == "6" and run.double_calls == [3]
    assert len(run.children) == 1 and run.children[0]["callKey"]
    # Resumed, not lost; and no step numbered twice.
    assert "LOST" not in run.kinds() and run.kinds()[-1] == "COMPLETED"
    steps = [e["step"] for e in run.events if e["agentPos"] is not None]
    assert len(steps) == len(set(steps)), steps


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_calls_are_found_again_by_their_keys(deployment: Deployment, tmp_path: Path) -> None:
    run = await kill_and_take_over(deployment, tmp_path, serve="gathered", call={"x": 3})

    assert run.error is None, (run.error, run.kinds())
    assert run.result == "6:8"
    assert sorted(run.double_calls) == [3, 4], "each call ran once, whatever order their steps took"
    assert len({c["callKey"] for c in run.children}) == 2


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_workflow_is_not_resumed_onto_changed_code(deployment: Deployment, tmp_path: Path) -> None:
    run = await kill_and_take_over(
        deployment, tmp_path, serve="pipeline", call={"x": 3}, successor_declares=declare_pipeline_changed
    )

    assert isinstance(run.error, AgentLost), run.error
    assert "code changed" in str(run.error)
    assert run.kinds()[-1] == "LOST"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_hold_survives_its_agent_dying(deployment: Deployment, tmp_path: Path) -> None:
    async def resume_once_held_again(app: Any, task_id: str) -> None:  # noqa: ANN401
        # Nobody had resumed it before the kill, so the resumed run holds again...
        await _eventually(lambda: _paused(2)(app, task_id), RESUME_TIMEOUT, "the resumed run to hold again")
        # ...and a person resumes it now.
        await app.rath.aquery(RESUME, {"id": task_id})

    run = await kill_and_take_over(
        deployment, tmp_path, serve="held", call={}, reached=_paused(1), after_take_over=resume_once_held_again
    )

    assert run.error is None, (run.error, run.kinds())
    assert run.result == pytest.approx(run.effects("NOW")[0]["value"])
    held = [e for e in run.events if e["kind"] == "PAUSED"]
    assert held[0]["message"] == "Check the plate, then resume."
    assert len(run.effects("HOLD")) == 1, "the resume is recorded once"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_workflow_handles_a_step_whose_agent_died(deployment: Deployment, tmp_path: Path) -> None:
    """The workflow lives; the agent of the step it called dies. It gets AgentLost, and decides."""
    port = rekuest_port(deployment)
    orchestrator = build_fresh_rekuest(deployment, token="workflow_token")
    declare_handles_a_lost_step(orchestrator)
    worker = Worker(tmp_path, port, "hang")

    async with orchestrator:
        await worker.start()
        await orchestrator.aconnect(timeout=CONNECT_TIMEOUT)
        loop = asyncio.create_task(orchestrator.aloop())
        try:
            impl = await orchestrator.amy_implementation_at("careful")
            pending = asyncio.create_task(orchestrator.acall(impl, x=3))
            await worker.wait_for(lambda: worker.marker.exists(), "the step to reach its crash point")
            await worker.kill_once_all_is_acknowledged()

            # A new process under the step's agent: the server now knows the old one is gone.
            successor = build_rekuest_at(port, "durable_token", name=AGENT_NAME, journal_path=str(worker.journal))
            declare_hang(successor)
            async with successor:
                await successor.aconnect(timeout=CONNECT_TIMEOUT)
                result = await asyncio.wait_for(pending, RESUME_TIMEOUT)
        finally:
            worker.kill()
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)

    assert result == "lost:True:60:IRREVERSIBLE"


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_values_the_dead_process_never_sent_come_back_too(deployment: Deployment, tmp_path: Path) -> None:
    """Killed at once: what it took may still be in its journal on disk, not on the server."""
    run = await kill_and_take_over(deployment, tmp_path, serve="stamped", call={}, wait_for_acks=False)

    assert run.error is None, (run.error, run.kinds())
    now, drawn = run.result.split(":")
    assert (len(run.effects("NOW")), len(run.effects("RANDOM"))) == (1, 1), run.kinds()
    assert float(now) == pytest.approx(run.effects("NOW")[0]["value"])
    assert drawn == run.effects("RANDOM")[0]["value"]
