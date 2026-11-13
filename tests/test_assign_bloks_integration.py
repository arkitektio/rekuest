"""An action's own bloks for starting a run of it, beside those that show one.

An app declares them with ``assign_bloks=``: shown before there is a task, with the
arguments being filled in as ``args``, the argument form as ``<args />`` and
``assign.start()`` to start the run. The server keeps them apart from the run's
(``Action.assignBloks`` / ``Action.bloks``), one per context and phase, so a client that
draws a run never picks up the blok for starting one.
"""

import asyncio

import pytest
from dokker import Deployment

from rekuest.protocol.schema import ActionBlokPhase

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest

WATCH = "<Html><Card><ask /></Card></Html>"
PREPARE = """
<Html>
  <Card>
    <Text>{args.printer}</Text>
    <args />
    <Button onClick={assign.start()}>Start</Button>
  </Card>
</Html>
"""


def _components(tree: object) -> set[str]:
    """Every component name in a blok's tree (``componentTree``, the wire JSON)."""
    if isinstance(tree, list):
        return set().union(*(_components(node) for node in tree))
    if not isinstance(tree, dict):
        return set()
    found = {tree["component"]} if isinstance(tree.get("component"), str) else set()
    return found.union(*(_components(value) for value in tree.values()))


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_actions_assign_bloks_are_registered_and_read_apart_from_its_runs(deployment: Deployment) -> None:
    app = build_fresh_rekuest(deployment, token="standalone_token")

    def stitch(printer: str) -> str:
        """Stitch a list of images."""
        return "stitched-" + printer

    def plain(printer: str) -> str:
        """An action with no bloks of its own."""
        return printer

    app.register_workflow(stitch, bloks=WATCH, assign_bloks=PREPARE)
    app.register(plain)

    async with app as app:
        await app.aconnect(timeout=CONNECT_TIMEOUT)
        loop = asyncio.create_task(app.aloop())
        try:
            action = (await app.amy_implementation_at("stitch")).action.id
            read = await app.aaction_bloks(action)

            [run] = read.bloks
            [start] = read.assign_bloks
            assert (run.phase, run.context) == (ActionBlokPhase.RUN, "HTML")
            assert (start.phase, start.context) == (ActionBlokPhase.ASSIGN, "HTML")
            assert run.blok.id != start.blok.id, "one blok per context and phase"

            starting, watching = _components(start.blok.component_tree), _components(run.blok.component_tree)
            assert "args" in starting and "args" not in watching
            assert "ask" in watching and "ask" not in starting

            # What a run shows is still only the run's.
            reference = "assign-bloks-run"
            assert await asyncio.wait_for(app.acall(action, printer="p", reference=reference), 30) == "stitched-p"
            tasks = (await app.rath.aquery("query { tasks { id reference } }", {})).data["tasks"]
            task = next(t["id"] for t in tasks if t["reference"] == reference)
            view = await app.atask_run_view(task)
            assert [(b.phase, b.blok.id) for b in view.action_bloks] == [(ActionBlokPhase.RUN, run.blok.id)]

            other = await app.aaction_bloks((await app.amy_implementation_at("plain")).action.id)
            assert other.bloks == () and other.assign_bloks == ()
        finally:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)
