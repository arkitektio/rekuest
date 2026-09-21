"""Top-level public API for rekuest.

Everything an app offers is declared on the app: ``AppRegistry`` (arkitekt's
``App`` delegates to it) is the one declaration surface, and there is no
process-wide registry to fall back to::

    from rekuest import AppRegistry, Task, model_field

    app = AppRegistry()

    @app.startup
    async def boot() -> None: ...

    @app.register
    def measure(x: int, task: Task) -> int: ...

Declared through the app: ``@app.register`` (actions), ``@app.state``,
``@app.context``, ``@app.app_context``, ``@app.model``, ``@app.startup``,
``@app.shutdown``, ``@app.background``, ``@app.declare`` (protocols another
app fulfils), ``@app.structure``, ``@app.service``, ``app.register_blok`` and
``app.register_memory_structure``.

What this module exports are values that go inside a declaration, not
declarations of their own: ``Task`` (inject it as ``task: Task`` to log, report
progress and pause), ``model_field`` for a model's fields, ``bsx`` and
``parse_util_call`` for bloks, ``withValidator``/``withEffect`` and the
``Requires``/``Provides``/``Description``/``Default``/``Units`` markers for
``Annotated`` ports, and the connection and disconnect policies. Demand
overrides for a declared protocol come from ``rekuest.declare`` (``demand``,
``demand_state``). Calling other actions goes through the ``Rekuest`` client
an action is handed (``rekuest: Rekuest`` -> ``rekuest.call(...)``).

Importing this package loads the declaration surface only. The rekuest service
and its agent provider live in :mod:`rekuest.arkitekt` (``rekuest_service``,
``rekuest_provider``), which is what brings in the socket runtime and the
GraphQL client; ``arkitekt`` re-exports everything here, so an app author
imports ``arkitekt`` alone.
"""

import importlib.metadata

from .blok.parser import bsx, parse_util_call
from .widgets import withEffect, withValidator
from .task import Task
from .actors.policy import (
    CancelOnDisconnect,
    DisconnectPolicy,
    OnDisconnect,
)
from .agents.policy import Backoff, ConnectionPolicy
from .structures.model import model_field
from .app import AppRegistry
from rekuest.annotations import (
    Requires,
    Provides,
    Description,
    Default,
    Units,
)



# The version lives only in the git tag (see [tool.hatch.version]); read the
# installed distribution metadata instead of keeping a copy here to bump.
__version__ = importlib.metadata.version("rekuest")

__all__ = [
    "Backoff",
    "CancelOnDisconnect",
    "ConnectionPolicy",
    "DisconnectPolicy",
    "OnDisconnect",
    # values that go inside a declaration
    "model_field",
    "bsx",
    "parse_util_call",
    "withValidator",
    "withEffect",
    # the task an action runs for (inject it: `task: Task`)
    "Task",
    # registry helpers
    "AppRegistry",
    "Requires",
    "Provides",
    "Description",
    "Default",
    "Units",
]
