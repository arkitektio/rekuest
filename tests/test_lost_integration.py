"""A plain action whose agent is killed mid-task ends LOST, and the caller gets ``AgentLost``.

The server re-runs nothing on its own: whoever called decides, with what is known. Here the
caller is a script (``call`` over GraphQL); the killed agent's successor registering with a
new session is what tells the server the old process is gone.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest
from dokker import Deployment

from arkitekt_spec.declare.errors import AgentLost

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest, build_rekuest_at, rekuest_port
from .durable_workflow import AGENT_NAME, CRASH_MARKER_ENV, declare_hang

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
WORKER_TIMEOUT = 60
LOST_TIMEOUT = 30

HISTORY = """
query History($id: ID!) {
  task(id: $id) {
    latestEventKind
    events(ordering: [{createdAt: ASC}]) { kind value }
  }
}
"""


async def _wait_for(path: Path, what: str, log: Path) -> None:
    for _ in range(int(WORKER_TIMEOUT / 0.25)):
        if path.exists():
            return
        await asyncio.sleep(0.25)
    tail = "\n".join(log.read_text().splitlines()[-40:])
    raise AssertionError(f"timed out waiting for {what}; the worker's log ends:\n{tail}")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_plain_task_whose_agent_dies_is_lost_and_the_caller_hears_it(
    deployment: Deployment, tmp_path: Path
) -> None:
    port = rekuest_port(deployment)
    journal, marker, ready, log = (tmp_path / n for n in ("journal.db", "reached", "ready", "worker.log"))
    caller = build_fresh_rekuest(deployment, token="durable_token")

    async with caller:
        with log.open("w") as log_file:
            worker = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "tests.durable_worker", cwd=PACKAGE_ROOT,
                env={
                    **os.environ,
                    "DURABLE_PORT": str(port),
                    "DURABLE_JOURNAL": str(journal),
                    "DURABLE_READY": str(ready),
                    "DURABLE_DECLARE": "hang",
                    CRASH_MARKER_ENV: str(marker),
                },
                stdout=log_file, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                await _wait_for(ready, "the worker to connect", log)
                impl = await caller.amy_implementation_at("hang")
                reference = f"lost-{uuid.uuid4().hex[:8]}"
                call = asyncio.create_task(caller.acall(impl, x=3, reference=reference))
                await _wait_for(marker, "the worker to reach its crash point", log)
            finally:
                worker.kill()
                await worker.wait()

        # A new process under the same agent: the server now knows the old one is gone.
        successor = build_rekuest_at(port, "durable_token", name=AGENT_NAME, journal_path=str(journal))
        declare_hang(successor)
        async with successor:
            await successor.aconnect(timeout=CONNECT_TIMEOUT)
            loop = asyncio.create_task(successor.aloop())
            try:
                with pytest.raises(AgentLost) as lost:
                    await asyncio.wait_for(call, LOST_TIMEOUT)
            finally:
                loop.cancel()
                await asyncio.gather(loop, return_exceptions=True)

        tasks = (await caller.rath.aquery("query { tasks { id reference } }", {})).data["tasks"]
        task_id = next(t["id"] for t in tasks if t["reference"] == reference)
        history = (await caller.rath.aquery(HISTORY, {"id": task_id})).data["task"]

    assert (lost.value.started, lost.value.last_progress, lost.value.effects) == (True, 60, "IRREVERSIBLE")
    kinds = [e["kind"] for e in history["events"]]
    assert history["latestEventKind"] == "LOST" and kinds.count("LOST") == 1, kinds
