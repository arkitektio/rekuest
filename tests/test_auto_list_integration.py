"""An approval can be remembered: the user's auto list of actions that run without asking.

Under ASK a child an agent asks for waits for its person. Deciding it with ``remember``
leaves an allowance, so the same action is not asked for again: for the rest of the run
(``RUN``), whenever the same action asks (``ASKER``), or whoever asks (``ALWAYS``). The
list is the user's own: read with ``my_allowances``, written with ``allow_action`` and
``revoke_allowance`` from the client of who may steer tasks.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol

import pytest
from dokker import Deployment

from arkitekt_spec.declare.errors import DeniedError
from rekuest.api.schema import ActionAllowance
from arkitekt_runtime.types import HoldRemember

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest

TASKS = "query { tasks { id reference latestEventKind } }"
EVENTS = "query Events($id: ID!) { task(id: $id) { latestEventKind events(ordering: [{createdAt: ASC}]) { kind message value } } }"
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


async def _root(app: Any, reference: str) -> str:  # noqa: ANN401
    async def root() -> str | None:
        tasks = (await _query(app, TASKS))["tasks"]
        return next((t["id"] for t in tasks if t["reference"] == reference), None)

    return await _eventually(root, "the root to exist")


async def _events(app: Any, task: str, kind: str) -> list[dict]:  # noqa: ANN401
    events = (await _query(app, EVENTS, {"id": task}))["task"]["events"]
    return [e["value"] or {} for e in events if e["kind"] == kind]


async def _held(app: Any, root: str, after: int = 0) -> dict:  # noqa: ANN401
    """The root's next HELD naming a child that waits, past the ``after`` it already showed."""

    async def held() -> dict | None:
        waiting = [value for value in await _events(app, root, "HELD") if value.get("held")]
        return waiting[after] if len(waiting) > after else None

    return await _eventually(held, "the root to show a held child")


@asynccontextmanager
async def _stage(deployment: Deployment) -> AsyncIterator[tuple[Any, Any, Any]]:  # noqa: ANN401
    """A provider of ``do_stuff``, a workflow app asking for it three ways, and the person's client."""
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
        """Ask for the provider's action once."""
        try:
            return atest.do_stuff("one")
        except DeniedError:
            return "denied"

    def asks_twice(atest: ATestLike) -> str:
        """Ask for the provider's action, and again."""
        try:
            return atest.do_stuff("a") + "+" + atest.do_stuff("b")
        except DeniedError:
            return "denied"

    def asks_too(atest: ATestLike) -> str:
        """Another action asking for the same one."""
        try:
            return atest.do_stuff("too")
        except DeniedError:
            return "denied"

    for workflow in (asks, asks_twice, asks_too):
        workflow_app.register_workflow(workflow)
    decider = build_fresh_rekuest(deployment, token="workflow_decider_token")

    async with provider as provider, workflow_app as workflow_app, decider as decider:
        await provider.aconnect(timeout=CONNECT_TIMEOUT)
        loops = [asyncio.create_task(provider.aloop())]
        await workflow_app.aconnect(timeout=CONNECT_TIMEOUT)
        loops.append(asyncio.create_task(workflow_app.aloop()))
        try:
            yield provider, workflow_app, decider
        finally:
            # A standing allowance outlives its test: the next one expects to be asked.
            for allowance in await decider.amy_allowances():
                await decider.arevoke_allowance(allowance.id)
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)


async def _start(workflow_app: Any, interface: str) -> tuple[asyncio.Task, str]:  # noqa: ANN401
    """Start a run of the workflow's ``interface`` under ASK; the pending call and its root."""
    impl = await workflow_app.amy_implementation_at(interface)
    reference = f"{interface}-{uuid.uuid4().hex[:8]}"
    pending = asyncio.create_task(workflow_app.acall(impl, reference=reference))
    return pending, await _root(workflow_app, reference)


async def _is_asked_and_denied(workflow_app: Any, decider: Any, interface: str) -> None:  # noqa: ANN401
    """A run of ``interface`` is asked for its child; denying it ends the run's question."""
    pending, root = await _start(workflow_app, interface)
    held = await _held(workflow_app, root)
    await decider.adecide(held["held"], False)
    assert await asyncio.wait_for(pending, TIMEOUT) == "denied"


async def _runs_unasked(workflow_app: Any, interface: str, scope: str) -> ActionAllowance | dict:  # noqa: ANN401
    """A run of ``interface`` gets its child without a hold; the DECIDED that says why."""
    pending, root = await _start(workflow_app, interface)
    assert await asyncio.wait_for(pending, TIMEOUT) == "stitched-one"
    assert [v for v in await _events(workflow_app, root, "HELD") if v.get("held")] == [], "nobody was asked"
    [decided] = await _events(workflow_app, root, "DECIDED")
    assert decided["approved"] is True and decided["auto"] is True
    assert decided["allowance"]["scope"] == scope
    return decided


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_approval_remembered_for_the_run_is_not_asked_again_in_it(deployment: Deployment) -> None:
    async with _stage(deployment) as (_, workflow_app, decider):
        pending, root = await _start(workflow_app, "asks_twice")
        held = await _held(workflow_app, root)
        assert held["kind"] == "assign"
        assert held["caller_action"]["name"], "the hold names the action that asks"

        await decider.adecide(held["held"], True, remember=HoldRemember.RUN)
        assert await asyncio.wait_for(pending, TIMEOUT) == "stitched-a+stitched-b"

        assert len([v for v in await _events(workflow_app, root, "HELD") if v.get("held")]) == 1, "the second call was not held"
        remembered, allowed = await _events(workflow_app, root, "DECIDED")
        assert remembered["remember"] == "RUN" and not remembered.get("auto")
        assert allowed["auto"] is True and allowed["allowance"]["scope"] == "run"
        assert allowed["held"] != held["held"], "it is the second child that ran unasked"

        # The allowance is the run's: it is on no list, and another run asks again.
        assert await decider.amy_allowances() == ()
        await _is_asked_and_denied(workflow_app, decider, "asks_twice")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_approval_remembered_for_its_asker_holds_in_every_run_of_that_asker_only(deployment: Deployment) -> None:
    async with _stage(deployment) as (provider, workflow_app, decider):
        pending, root = await _start(workflow_app, "asks")
        held = await _held(workflow_app, root)
        await decider.adecide(held["held"], True, remember=HoldRemember.ASKER)
        assert await asyncio.wait_for(pending, TIMEOUT) == "stitched-one"

        [allowance] = await decider.amy_allowances()
        assert allowance.key == "do_stuff" and allowance.asker_key == "asks"
        assert allowance.action is not None and allowance.asker is not None
        target = (await provider.amy_implementation_at("do_stuff")).action.id
        assert allowance.action.id == target
        assert [a.id for a in await decider.amy_allowances(action=target)] == [allowance.id]

        await _runs_unasked(workflow_app, "asks", scope="always")
        # Another action asking for the same one is still asked.
        await _is_asked_and_denied(workflow_app, decider, "asks_too")

        assert await decider.arevoke_allowance(allowance.id) == allowance.id
        assert await decider.amy_allowances() == ()
        await _is_asked_and_denied(workflow_app, decider, "asks")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_action_put_on_the_auto_list_runs_whoever_asks_until_it_is_taken_off(deployment: Deployment) -> None:
    async with _stage(deployment) as (provider, workflow_app, decider):
        target = (await provider.amy_implementation_at("do_stuff")).action.id

        allowance = await decider.aallow_action(target)
        assert allowance.key == "do_stuff" and allowance.asker is None and allowance.asker_key is None
        assert (await decider.aallow_action(target)).id == allowance.id, "allowing it twice is one entry"

        await _runs_unasked(workflow_app, "asks", scope="always")
        pending, root = await _start(workflow_app, "asks_too")
        assert await asyncio.wait_for(pending, TIMEOUT) == "stitched-too"
        assert [v for v in await _events(workflow_app, root, "HELD") if v.get("held")] == []

        # Bound to one asker, it is its own entry beside the general one.
        asker = (await workflow_app.amy_implementation_at("asks")).action.id
        bound = await decider.aallow_action(target, asker=asker)
        assert bound.id != allowance.id and bound.asker_key == "asks"
        assert {a.id for a in await decider.amy_allowances(action=target)} == {allowance.id, bound.id}

        for entry in (allowance, bound):
            await decider.arevoke_allowance(entry.id)
        await _is_asked_and_denied(workflow_app, decider, "asks")
