"""rekuest: the distributed runtime for Arkitekt apps.

An app is declared with :mod:`arkitekt_spec.declare` (``arkitekt`` re-exports it:
``App``, ``@app.action``, ``@app.state``, the annotation markers, widgets, bloks).
rekuest runs that declaration in distributed mode: an agent registers it with a
rekuest server over a websocket, is assigned work, and runs each action as an actor.
The FastAPI runtime (:mod:`rekuest.contrib.fastapi`) serves the same declaration
over HTTP instead.

What this package exports are the runtime's own values: :class:`Task` (what an
action is handed as ``task: Task`` while it runs) and the agent's connection
policies. The ``Rekuest`` client and the socket runtime are :mod:`rekuest.client`
and :mod:`rekuest.arkitekt` (``rekuest_service``, ``rekuest_provider``).
"""

import importlib.metadata

from .agents.policy import Backoff, ConnectionPolicy
from .task import Task

# The version lives only in the git tag (see [tool.hatch.version]); read the
# installed distribution metadata instead of keeping a copy here to bump.
__version__ = importlib.metadata.version("rekuest")

__all__ = [
    "Backoff",
    "ConnectionPolicy",
    "Task",
]
