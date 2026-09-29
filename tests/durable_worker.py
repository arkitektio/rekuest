"""The agent process the durable workflow test kills with SIGKILL. Not a test module.

Run from the package root as ``python -m tests.durable_worker``, with ``DURABLE_PORT``,
``DURABLE_JOURNAL``, ``DURABLE_READY`` and ``DURABLE_CRASH_MARKER`` set, and ``DURABLE_DECLARE``
naming what it serves (``pipeline`` by default). It serves that until
killed: a real kill is the crash, since an orderly teardown would cancel the task and
report it CANCELLED.
"""

import asyncio
import logging
import os
from pathlib import Path

from .conftest import CONNECT_TIMEOUT, build_rekuest_at
from .durable_workflow import AGENT_NAME, DECLARE


async def main() -> None:
    app = build_rekuest_at(
        int(os.environ["DURABLE_PORT"]),
        "durable_token",
        name=AGENT_NAME,
        journal_path=os.environ["DURABLE_JOURNAL"],
    )
    DECLARE[os.environ.get("DURABLE_DECLARE", "pipeline")](app)
    async with app:
        await app.aconnect(timeout=CONNECT_TIMEOUT)
        Path(os.environ["DURABLE_READY"]).write_text("connected")
        await app.aloop()


if __name__ == "__main__":
    # To the log file the test hands us: it shows the tail when the worker gets stuck.
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
