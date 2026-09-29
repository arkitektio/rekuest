"""A durable workflow: an idempotent task whose agent is killed mid-task, and taken over.

The workflow (``tests/durable_workflow.py``) takes the clock, a random draw and maybe a
child call on a provider, then blocks. The test kills its agent process with SIGKILL and
starts a new one under the same agent name and journal. The new process registers with a
new session, so the server treats the old one as dead and re-dispatches the idempotent task
to it. The call, made over GraphQL, waits across all of this.

What passes today is the re-run itself: the server re-queues the task, the new process runs
it to completion, and a child call it re-issues finds the child it already made. (That needs
the task to have reported STARTED: until then the server counts it as QUEUED, and takes it for
one it already re-queued.)

What does not is **replay**: the re-run runs from scratch. The ``xfail(strict=True)`` tests
below pin what a replay engine must add (``docs/design/journal.md``: "When a replay engine exists,
each one will first look up the recorded value at its step"). Each flips to XPASS, and so
fails, the day it works: that is the signal to drop its marker.
"""

import asyncio
import os
import sys
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from dokker import Deployment

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest, build_rekuest_at, rekuest_port
from .durable_workflow import AGENT_NAME, CRASH_MARKER_ENV, declare_pipeline

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

#: How long the killed process may take to register and reach its crash point.
WORKER_TIMEOUT = 60
#: How long the re-run gets to finish once the new process holds the agent.
RERUN_TIMEOUT = 30
#: How long to wait for the re-run's own effects to reach the server.
HISTORY_TIMEOUT = 10

# Parked: registration no longer takes idempotent=True (the recovery redesign drops it).
# Phase 5 of the workflows plan rewrites this module onto @app.workflow.
pytestmark = pytest.mark.skip(reason="rewritten onto @app.workflow in Phase 5 of the workflows plan")

REPLAY_MISSING = (
    "no replay engine: a re-dispatched task re-runs from scratch (docs/design/journal.md)"
)

IDEMPOTENT = """
query Idempotent($id: ID!) {
  implementation(id: $id) { action { idempotent } }
}
"""

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


@dataclass
class CrashRun:
    """What one kill-and-take-over run left behind."""

    result: str | None
    """The call's return value, or None when it did not finish within ``RERUN_TIMEOUT``."""
    double_calls: int
    """How often the provider's ``double`` actually ran."""
    children: list[dict[str, Any]]
    events: list[dict[str, Any]]

    @property
    def requeued(self) -> bool:
        """Whether the server re-queued the task. Its first dispatch writes no QUEUED event."""
        return any(e["kind"] == "QUEUED" for e in self.events)

    def effects(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["kind"] == "EFFECT" and e["effect"] == kind]


async def _eventually(
    probe: Callable[[], Awaitable[Any]], timeout: float, what: str
) -> Any:  # noqa: ANN401
    """Await ``probe`` until it returns something truthy without raising."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            value = await probe()
            if value:
                return value
        except Exception:  # noqa: BLE001 - not there yet
            if time.monotonic() > deadline:
                raise
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.25)


async def _stop(*loops: asyncio.Task[Any]) -> None:
    for loop in loops:
        loop.cancel()
        try:
            await loop
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - it is being torn down
            pass


async def run_crash_scenario(
    deployment: Deployment, scratch: Path, *, with_child: bool
) -> CrashRun:
    """Call ``pipeline``, kill its agent at the crash point, and let a new process take over."""
    port = rekuest_port(deployment)
    journal = scratch / "journal.db"
    marker = scratch / "reached"
    worker_log = scratch / "worker.log"
    ready = scratch / "ready"

    calls: list[int] = []

    def double(x: int) -> int:
        """Double a number."""
        calls.append(x)
        return 2 * x

    provider = build_fresh_rekuest(deployment, token="atest_token")
    provider.register(double)
    # The caller calls over GraphQL as the `durable` app; its own agent is never connected.
    caller = build_fresh_rekuest(deployment, token="durable_token")

    async with provider, caller:
        await provider.aconnect(timeout=CONNECT_TIMEOUT)
        provider_loop = asyncio.create_task(provider.aloop())

        log_file = worker_log.open("w")
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.durable_worker",
            cwd=PACKAGE_ROOT,
            # The whole environment, so the worker sees the same PYTHONPATH (local sources).
            env={
                **os.environ,
                "DURABLE_PORT": str(port),
                "DURABLE_JOURNAL": str(journal),
                CRASH_MARKER_ENV: str(marker),
                "DURABLE_READY": str(ready),
            },
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
        )
        call: asyncio.Task[Any] | None = None
        try:
            # Not just "pipeline exists": in a second scenario it does from the first one's
            # agent, and a call made before this worker holds the agent never reaches it.
            await _eventually(
                lambda: asyncio.sleep(0, ready.exists()),
                WORKER_TIMEOUT,
                "the worker to connect",
            )
            impl = await caller.amy_implementation_at("pipeline")
            # Without this the server leaves the killed task DISCONNECTED, and the test
            # fails as an unexplained timeout (a stale server image, or a venv whose
            # arkitekt-spec cannot declare idempotent yet).
            declared = await caller.rath.aquery(IDEMPOTENT, {"id": impl.id})
            assert declared.data["implementation"]["action"]["idempotent"], (
                "pipeline did not register as idempotent"
            )

            reference = f"durable-{uuid.uuid4().hex[:8]}"
            call = asyncio.create_task(
                caller.acall(impl, x=3, with_child=with_child, reference=reference)
            )
            try:
                await _eventually(
                    lambda: asyncio.sleep(0, marker.exists()),
                    WORKER_TIMEOUT,
                    "the worker to reach its crash point",
                )
            except AssertionError as e:
                tail = "\n".join(worker_log.read_text().splitlines()[-40:])
                raise AssertionError(f"{e}; the worker's log ends:\n{tail}") from None
            assert not call.done(), "the call ended before the agent was killed"
        finally:
            worker.kill()
            await worker.wait()
            log_file.close()

        successor = build_rekuest_at(
            port, "durable_token", name=AGENT_NAME, journal_path=str(journal)
        )
        declare_pipeline(successor)
        try:
            async with successor:
                # Retries while the server still holds the killed process's lease.
                await successor.aconnect(timeout=CONNECT_TIMEOUT)
                successor_loop = asyncio.create_task(successor.aloop())

                # Not wait_for: a timeout must not cancel the call before its history is read.
                await asyncio.wait({call}, timeout=RERUN_TIMEOUT)
                result = call.result() if call.done() and not call.exception() else None

                parent = await _eventually(
                    lambda: _task_by_reference(caller, reference), 5, "the parent task"
                )

                async def rerun_effects_arrived() -> list[dict[str, Any]] | None:
                    events = await _history(caller, parent["id"])
                    nows = [e for e in events if e["effect"] == "NOW"]
                    # A replaying engine records once; today the re-run records again.
                    return events if len(nows) >= 2 or result is not None else None

                try:
                    events = await _eventually(
                        rerun_effects_arrived, HISTORY_TIMEOUT, "the re-run's effects"
                    )
                except AssertionError:
                    events = await _history(caller, parent["id"])

                seen = {t["id"]: t for t in [*await _tasks(caller), *await _tasks(provider)]}
                children = [
                    t for t in seen.values() if (t["parent"] or {}).get("id") == parent["id"]
                ]
                await _stop(call, successor_loop)
        finally:
            await _stop(provider_loop)

    return CrashRun(result=result, double_calls=len(calls), children=children, events=events)


async def _tasks(app: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    return (await app.rath.aquery(TASKS, {})).data["tasks"]


async def _task_by_reference(app: Any, reference: str) -> dict[str, Any] | None:  # noqa: ANN401
    return next((t for t in await _tasks(app) if t["reference"] == reference), None)


async def _history(app: Any, task_id: str) -> list[dict[str, Any]]:  # noqa: ANN401
    return (await app.rath.aquery(HISTORY, {"id": task_id})).data["task"]["events"]


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def rerun(
    deployment: Deployment, tmp_path_factory: pytest.TempPathFactory
) -> AsyncGenerator[CrashRun, None]:
    """The workflow without a child call, killed after its effects."""
    yield await run_crash_scenario(
        deployment, tmp_path_factory.mktemp("durable-rerun"), with_child=False
    )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def rerun_with_child(
    deployment: Deployment, tmp_path_factory: pytest.TempPathFactory
) -> AsyncGenerator[CrashRun, None]:
    """The workflow with a child call, killed after the child finished."""
    yield await run_crash_scenario(
        deployment, tmp_path_factory.mktemp("durable-child"), with_child=True
    )


# -- the re-run: what works today ----------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_idempotent_task_is_rerun_after_its_agent_is_killed(rerun: CrashRun) -> None:
    assert rerun.requeued, [e["kind"] for e in rerun.events]
    assert rerun.result is not None, [e["kind"] for e in rerun.events]
    assert rerun.result.startswith("6:"), rerun.result

    kinds = [e["kind"] for e in rerun.events]
    # Started by the killed process, re-queued when the new one took over, started again.
    first_start = kinds.index("STARTED")
    requeued = kinds.index("QUEUED", first_start)
    assert "STARTED" in kinds[requeued:], kinds
    terminal = [k for k in kinds if k in {"COMPLETED", "FAILED", "CRITICAL", "CANCELLED"}]
    assert terminal == ["COMPLETED"], kinds


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_rerun_child_call_is_deduplicated(rerun_with_child: CrashRun) -> None:
    # Without a re-run the provider ran once trivially.
    assert rerun_with_child.requeued
    # The re-run takes the same step for its child call, and the server hands back the
    # child it already made instead of running the provider again.
    assert rerun_with_child.double_calls == 1
    assert len(rerun_with_child.children) == 1, rerun_with_child.children
    assert rerun_with_child.children[0]["parentStep"] is not None


# -- replay: what a replay engine must add -------------------------------------------------
# Each first requires the re-run: without one, "each effect once" would pass vacuously.


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.xfail(strict=True, reason=REPLAY_MISSING)
async def test_a_rerun_returns_its_recorded_effects(rerun: CrashRun) -> None:
    assert rerun.requeued
    assert rerun.result is not None
    _, now, drawn = rerun.result.split(":")
    first_now, first_random = rerun.effects("NOW")[0], rerun.effects("RANDOM")[0]
    assert float(now) == pytest.approx(first_now["value"])
    assert drawn == first_random["value"]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.xfail(strict=True, reason=REPLAY_MISSING)
async def test_a_rerun_records_each_effect_once(rerun: CrashRun) -> None:
    assert rerun.requeued
    assert (len(rerun.effects("NOW")), len(rerun.effects("RANDOM"))) == (1, 1)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.xfail(strict=True, reason=REPLAY_MISSING + "; steps restart at 1")
async def test_task_steps_continue_across_a_restart(rerun: CrashRun) -> None:
    assert rerun.requeued
    steps = [e["step"] for e in rerun.events if e["step"] is not None]
    assert len(steps) == len(set(steps)), steps


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.xfail(
    strict=True,
    reason="a re-issued child call whose child already finished gets created=False and "
    "no events, so the re-run waits on it forever",
)
async def test_a_rerun_completes_after_its_child_already_finished(
    rerun_with_child: CrashRun,
) -> None:
    assert rerun_with_child.requeued
    assert rerun_with_child.result is not None
    assert rerun_with_child.result.startswith("6:")
