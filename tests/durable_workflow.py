"""The workflow the durable workflow test kills mid-task, shared by both of its processes.

The killed process (``tests/durable_worker.py``) and the one that takes over (the test
itself) must declare the *same* action: the server re-dispatches the task to whichever
process holds the agent next, and a re-run only finds its child call again if it takes
the same steps in the same order.
"""

import os
import time
from pathlib import Path
from typing import Any, Protocol

from arkitekt_runtime.task import Task

#: Both processes run the agent under this name: retained frames are kept per agent name.
AGENT_NAME = "durable-workflow-agent"

#: Set (to a file path) only in the process that is to be killed: the task writes the
#: file once it has taken all its steps, then blocks until the test kills it.
CRASH_MARKER_ENV = "DURABLE_CRASH_MARKER"


def crash_point() -> None:
    """Block forever in the process the test kills; do nothing in any other."""
    marker = os.environ.get(CRASH_MARKER_ENV)
    if marker:
        Path(marker).write_text("reached")
        time.sleep(3600)


def declare_pipeline(app: Any) -> None:  # noqa: ANN401 - a FreshApp
    """Declare ``pipeline`` (idempotent) and its dependency on the ``atest`` provider."""

    @app.declare(app="atest", auto_resolvable=True, min=1)
    class Doubler(Protocol):
        def double(self, x: int) -> int:
            """Double a number."""
            ...

    def pipeline(atest: Doubler, x: int, with_child: bool, task: Task) -> str:
        """Take the clock and randomness, maybe double on the provider, and report all three."""
        # Every step-taking call before the crash point must be the same in both runs: a
        # re-run renumbers from step 1, and finds its child call again only at the same step.
        now = task.now()
        drawn = task.random(4)
        doubled = atest.double(x) if with_child else 2 * x
        crash_point()
        return f"{doubled}:{now}:{drawn}"

    app.register(idempotent=True)(pipeline)
