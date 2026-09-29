"""The actions the durable workflow tests kill mid-task, shared by both of their processes.

The killed process (``tests/durable_worker.py``) and the one that takes over (the test
itself) declare the same actions: the server resumes a workflow on whichever process holds
the agent next, and a resumed run only replays correctly if it runs the same code.
``DECLARE`` names what the worker serves (``DURABLE_DECLARE``).
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Protocol

from arkitekt_runtime.task import Task
from arkitekt_spec.actions import Effects
from arkitekt_spec.declare.errors import AgentLost

#: Both processes run the agent under this name: retained frames are kept per agent name.
AGENT_NAME = "durable-workflow-agent"

#: Set (to a file path) only in the process that is to be killed: the task writes the
#: file when it gets there, then blocks until the test kills it.
CRASH_MARKER_ENV = "DURABLE_CRASH_MARKER"


def crash_point() -> None:
    """Block forever in the process the test kills; do nothing in any other."""
    marker = os.environ.get(CRASH_MARKER_ENV)
    if marker:
        Path(marker).write_text("reached")
        time.sleep(3600)


async def acrash_point() -> None:
    """:func:`crash_point` for async code: it must not block the event loop."""
    marker = os.environ.get(CRASH_MARKER_ENV)
    if marker:
        Path(marker).write_text("reached")
        await asyncio.sleep(3600)


def _declare_doubler(app: Any) -> Any:  # noqa: ANN401 - a FreshApp
    @app.declare(app="atest", auto_resolvable=True, min=1)
    class Doubler(Protocol):
        def double(self, x: int) -> int:
            """Double a number."""
            ...

    return Doubler


def declare_pipeline(app: Any) -> None:  # noqa: ANN401 - a FreshApp
    """``pipeline``: takes the clock and randomness, doubles on the provider, then blocks."""
    Doubler = _declare_doubler(app)

    def pipeline(atest: Doubler, x: int, task: Task) -> str:  # type: ignore[valid-type]
        """Take the clock and randomness, and double on the provider."""
        now = task.now()
        drawn = task.random(4)
        doubled = atest.double(x)
        crash_point()
        return f"{doubled}:{now}:{drawn}"

    app.register_workflow(pipeline)


def declare_pipeline_changed(app: Any) -> None:  # noqa: ANN401 - a FreshApp
    """``pipeline`` again, with a different body: resuming onto it must be refused."""
    Doubler = _declare_doubler(app)

    def pipeline(atest: Doubler, x: int, task: Task) -> str:  # type: ignore[valid-type]
        """Take the clock and randomness, and double on the provider."""
        now = task.now()
        drawn = task.random(4)
        doubled = atest.double(x) + 0  # a change the code hash sees
        crash_point()
        return f"{doubled}:{now}:{drawn}"

    app.register_workflow(pipeline)


def declare_gathered(app: Any) -> None:  # noqa: ANN401 - a FreshApp
    """``gathered``: two calls to the provider at once, then blocks."""
    Doubler = _declare_doubler(app)

    async def gathered(atest: Doubler, x: int, task: Task) -> str:  # type: ignore[valid-type]
        """Double two numbers at once on the provider."""
        first, second = await asyncio.gather(atest.double.acall(x), atest.double.acall(x + 1))
        await acrash_point()
        return f"{first}:{second}"

    app.register_workflow(gathered)


def declare_held(app: Any) -> None:  # noqa: ANN401 - a FreshApp
    """``held``: takes the clock, then waits for a person."""

    def held(task: Task) -> float:
        """Take the clock, then wait for a person to resume it."""
        now = task.now()
        task.hold("Check the plate, then resume.")
        return now

    app.register_workflow(held)


def declare_hang(app: Any) -> None:  # noqa: ANN401 - a FreshApp
    """``hang``: a plain action that reports progress, then blocks where it is killed."""

    def hang(x: int, task: Task) -> int:
        """Report some progress, then wait at the crash point."""
        task.progress(60, "halfway")
        crash_point()
        return x

    app.register(effects=Effects.IRREVERSIBLE)(hang)


def declare_handles_a_lost_step(app: Any) -> None:  # noqa: ANN401 - a FreshApp
    """``careful``: a workflow calling ``hang`` on the durable app, handling its loss."""

    @app.declare(app="durable", auto_resolvable=True, min=1)
    class Hanger(Protocol):
        def hang(self, x: int) -> int:
            """Report some progress, then wait at the crash point."""
            ...

    def careful(durable: Hanger, x: int, task: Task) -> str:  # type: ignore[valid-type]
        """Call hang; if its agent dies, say what is known instead of failing."""
        try:
            return str(durable.hang(x))
        except AgentLost as lost:
            return f"lost:{lost.started}:{lost.last_progress}:{lost.effects}"

    app.register_workflow(careful)


#: What ``tests/durable_worker.py`` can serve, by ``DURABLE_DECLARE``.
DECLARE = {
    "pipeline": declare_pipeline,
    "gathered": declare_gathered,
    "held": declare_held,
    "hang": declare_hang,
}
