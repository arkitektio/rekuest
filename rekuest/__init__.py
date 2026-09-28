"""rekuest: the distributed runtime for Arkitekt apps.

An app is declared with :mod:`arkitekt_spec.declare` and executed by the core in
:mod:`arkitekt_runtime`. rekuest runs it in distributed mode: an agent registers the
declaration with a rekuest server over a websocket
(:mod:`rekuest.agents.transport.websocket`, :mod:`rekuest.agents.control`), is assigned
work, and calls other agents' actions through the server
(:mod:`rekuest.agents.caller`). The GraphQL client is :mod:`rekuest.client`;
:mod:`rekuest.arkitekt` declares its service and the agent provider ``run(app)`` uses.
"""

import importlib.metadata

# The version lives only in the git tag (see [tool.hatch.version]); read the
# installed distribution metadata instead of keeping a copy here to bump.
__version__ = importlib.metadata.version("rekuest")
