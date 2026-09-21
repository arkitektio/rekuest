"""Some configuration for pytest"""

import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import Any
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from uuid import uuid4
import pytest
from rekuest.app import AppRegistry
from rekuest.structures.registry import StructureRegistry
from rekuest.client.client import Rekuest, RekuestRath
from rath.links.testing.direct_succeeding_link import DirectSucceedingLink
from rekuest.agents.base import RekuestAgent
from rekuest.client.postman import GraphQLPostman
from rekuest.agents.transport.websocket import WebsocketAgentTransport
import os
from dokker import Deployment, testing
from dokker.log_watcher import LogWatcher
from rath.links.auth import ComposedAuthLink
from rath.links.aiohttp import AIOHttpLink
from rath.links.graphql_ws import GraphQLWSLink
from rath.links.split import SplitLink
from graphql import OperationType
import pytest_asyncio
from rath.links.compose import compose


# Maximum time (seconds) to wait for an agent to connect and be acknowledged by
# the server before a test fails. Used with ``app.aconnect(timeout=...)``.
CONNECT_TIMEOUT = 5


def _dump_asyncio_tasks(signum: object, frame: object) -> None:
    """SIGALRM handler: dump every pending asyncio task's stack to stderr.

    Used to locate which coroutine is stuck when a test stalls. Enabled via the
    ``STALL_DEBUG`` env var (seconds).
    """
    import asyncio
    import sys

    try:
        loop = asyncio.get_event_loop()
        tasks = asyncio.all_tasks(loop)
    except Exception as exc:  # pragma: no cover - debug helper
        sys.stderr.write(f"\n##### STALL DUMP failed: {exc} #####\n")
        sys.stderr.flush()
        return
    sys.stderr.write(f"\n##### STALL DUMP: {len(tasks)} pending tasks #####\n")
    for task in tasks:
        sys.stderr.write(f"\n--- TASK {task!r}\n")
        task.print_stack(file=sys.stderr)
    sys.stderr.flush()


@pytest.fixture(autouse=True)
def _stall_watchdog():  # noqa: ANN202
    """Dump asyncio task stacks if a test runs longer than ``STALL_DEBUG`` seconds."""
    import os
    import signal

    seconds = os.environ.get("STALL_DEBUG")
    if not seconds:
        yield
        return
    signal.signal(signal.SIGALRM, _dump_asyncio_tasks)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


class MockShelver:
    """A mock shelver that stores values in memory. This is used to test the
    shelver functionality without using a real shelver."""

    def __init__(self) -> None:
        """Initialize the mock shelver."""
        self.shelve: dict[str, object] = {}

    async def aput_on_shelve(self, identifier: str, value: object) -> str:
        """Put a value on the shelve and return the key. This is used to store
        values on the shelve."""
        key = str(id(value))
        self.shelve[key] = value
        return key

    async def aget_from_shelve(self, key: str) -> object:
        """Get a value from the shelve. This is used to get values from the
        shelve."""
        return self.shelve[key]


@pytest.fixture()
def simple_registry() -> StructureRegistry:
    """A registry preloaded with the structures the test functions use.

    Nothing registers itself any more, so the structures a signature names have
    to be in here before a definition can be built against it.
    """
    from .funcs import Karl, LocalizedStructure
    from .structures import test_registry

    registry = test_registry()
    # Lives in funcs.py beside the function that returns it, and is the one thing
    # here that is kept on the shelve rather than fetched by id.
    registry.register_as_memory_structure(LocalizedStructure)
    # The one model the test functions name; a model is declared on an app.
    registry.model(Karl)
    return registry


@pytest.fixture()
def mock_shelver() -> MockShelver:
    """Fixture for a mock shelver"""
    return MockShelver()


@pytest.fixture()
def mock_agent() -> RekuestAgent:
    """An agent with a transport that never connects and a registry of its own."""

    async def token_loader() -> str:
        """Mock token loader function."""
        return "mock_token"

    return RekuestAgent(
        transport=WebsocketAgentTransport(
            endpoint_url="ws://localhost:8000/graphql",
            token_loader=token_loader,
        ),
        name="Test",
    )


@pytest.fixture()
def mock_rekuest(mock_agent: RekuestAgent) -> Rekuest:
    """A client of the same registry as ``mock_agent``, knowing nothing of the agent."""
    rath = RekuestRath(link=DirectSucceedingLink())
    return Rekuest(
        rath=rath,
        postman=GraphQLPostman(rath=rath),
        structure_registry=mock_agent.app_registry.structure_registry,
    )


project_path = os.path.join(os.path.dirname(__file__), "integration")
docker_compose_file = os.path.join(project_path, "docker-compose.yml")
# An untracked sibling override (see its own header): when a developer's checkout sits next
# to a live rekuest source tree, it mounts that tree over the published image so the tests
# see the current schema instead of the last-pushed one. Absent (CI, anyone else), the
# published image is the schema under test, as before.
_local_override = os.path.join(project_path, "docker-compose.local.yml")
compose_files = [docker_compose_file] + (
    [_local_override] if os.path.exists(_local_override) else []
)


def _reserve_free_ports(count: int) -> list[int]:
    """Ask the OS for `count` distinct free TCP ports.

    All sockets are held open until every port has been assigned, so the
    kernel cannot hand out the same port twice within one call. They are
    released before compose binds them -- a race in theory, but the ephemeral
    range is large and this is what keeps concurrent runs (and the leftovers
    of a crashed one) from colliding on a fixed port.
    """
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [int(sock.getsockname()[1]) for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


@pytest.fixture(scope="session")
def integration_ports() -> Generator[dict[str, int], None, None]:
    """Pick this run's host ports and point compose at them.

    The ports are reserved here rather than left to docker (`ports: - "80"`)
    because `Deployment.spec` is rendered by `docker compose config`, which is
    static: an unpublished port reads back as ``None`` and the test URLs would
    quietly become ``http://localhost:None`` instead of failing loudly.

    Both stack fixtures depend on this, so neither can come up on a stale port.
    """
    rekuest_port, rustfs_port = _reserve_free_ports(2)
    env = {"REKUEST_HOST_PORT": str(rekuest_port), "RUSTFS_HOST_PORT": str(rustfs_port)}
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        yield {"rekuest": rekuest_port, "rustfs": rustfs_port}
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def token_loader() -> str:
    """Asynchronous function to load a token for authentication.

    This returns the "test" token which is configured as a static token to map to
    the user "test" in the test environment. In a real application, this function
    will return an oauth2 token or similar authentication token.

    To change this mapping you can alter the ``authentikate.static_tokens``
    configuration in the rekuest configuration file (inside the integration
    folder).

    """
    return "test"


def make_token_loader(token: str = "test") -> Callable[[], Awaitable[str]]:
    """Build a token loader that always returns ``token``.

    The integration deployment is configured (see ``authentikate.static_tokens``
    in ``tests/integration/configs/rekuest.yaml``) with several static tokens,
    each mapping to a *different* ``client_app``. Authenticating an app with a given
    token therefore makes the server treat it as that distinct application. This
    is what lets several :func:`build_fresh_rekuest` apps run side by side as
    genuinely separate apps (e.g. a workflow app calling a provider app).

    Args:
        token: The static token to authenticate as. Must be one of the tokens
            configured in the deployment (``test``, ``atest_token``,
            ``btest_token``, ``workflow_token``, ``standalone_token``).

    Returns:
        An async, no-argument token loader suitable for the auth link and agent
        transport.
    """

    async def _loader() -> str:
        return token

    return _loader


@dataclass
class DeployedRekuest:
    """Dataclass to hold the deployed Mikro application and its components."""

    deployment: Deployment
    rekuest_watcher: LogWatcher
    rustfs_watcher: LogWatcher
    rekuest: "FreshApp"


def most_basic_function(hello: str) -> str:
    """Karl

    Karl takes a a representation and does magic stuff

    Args:
        hallo (str): Nougat

    Returns:
        str: The Returned Representation
    """
    return hello + " world"


@pytest.fixture(scope="session")
def deployed_app(integration_ports: dict[str, int]) -> Generator[DeployedRekuest, None, None]:
    """Fixture to deploy the Mikro application with Docker Compose.

    This fixture sets up the Mikro application using Docker Compose,
    configures health checks, and provides a deployed instance of Mikro
    for testing purposes. It also includes watchers for the Mikro and RustFS
    services to monitor their logs, when performing requests against the application.

    Yields:
        DeployedMikro: An instance containing the deployment, watchers, and Mikro instance

    """
    setup = testing(compose_files)
    setup.add_health_check(
        url=lambda spec: (
            f"http://localhost:{spec.find_service('rekuest').get_port_for_internal(80).published}/graphql"
        ),
        service="rekuest",
        timeout=5,
        max_retries=10,
    )

    watcher = setup.create_watcher("rekuest")
    rustfs_watcher = setup.create_watcher("rustfs")

    with setup:
        setup.down()

        setup.pull()

        mikro_http_url = f"http://localhost:{setup.spec.find_service('rekuest').get_port_for_internal(80).published}/graphql"
        mikro_ws_url = f"ws://localhost:{setup.spec.find_service('rekuest').get_port_for_internal(80).published}/graphql"

        rath = RekuestRath(
            link=compose(
                ComposedAuthLink(
                    token_loader=token_loader, token_refresher=token_loader
                ),
                SplitLink(
                    left=AIOHttpLink(endpoint_url=mikro_http_url),
                    right=GraphQLWSLink(ws_endpoint_url=mikro_ws_url),
                    split=lambda o: o.node.operation != OperationType.SUBSCRIPTION,
                ),
            ),
        )

        agent = RekuestAgent(
            transport=WebsocketAgentTransport(
                endpoint_url=f"ws://localhost:{setup.spec.find_service('rekuest').get_port_for_internal(80).published}/agi",
                token_loader=token_loader,
            ),
            name="Test",
        )

        rekuest = FreshApp(
            client=Rekuest(
                rath=rath,
                postman=GraphQLPostman(rath=rath),
                structure_registry=agent.app_registry.structure_registry,
            ),
            agent=agent,
        )
        setup.up()

        rekuest.registry.register(most_basic_function)

        setup.check_health()

        with rekuest as rekuest:
            deployed = DeployedRekuest(
                deployment=setup,
                rekuest_watcher=watcher,
                rustfs_watcher=rustfs_watcher,
                rekuest=rekuest,
            )

            yield deployed


logger = logging.getLogger(__name__)

REGISTRATION_DRAIN_TIMEOUT = 45.0
"""How long a fresh client keeps retrying while the previous test's registration drains.

The server considers an incumbent live while ``connected`` is set and its heartbeat is
fresh (``AGENT_STALE_AFTER`` = 3 x 10s). Normally the incumbent's disconnect flips
``connected`` off within milliseconds of the socket closing; when that handler is
delayed, the stale sweep displaces it after 30s at the latest. 45s covers both.
"""


@dataclass
class FreshApp:
    """A client and the agent serving its registry, as a run would hold them.

    What the integration tests drive: register on ``registry``, ``aconnect`` and
    ``aloop`` the agent, call the API through ``client``. Attributes the app does
    not have are the client's, so a test reads ``app.acall`` and ``app.postman``
    off it directly.

    ``aconnect`` tolerates the previous test's registration: the server keys the
    agent registration on the *token* and only releases it asynchronously after
    the socket closes, so a fresh app may connect while the previous test's
    registration is still draining and get ``"Another connection is already
    registered for this agent"``. Retrying within the drain budget turns that
    race into a short wait instead of a flake.
    """

    client: Rekuest
    agent: RekuestAgent

    @property
    def registry(self) -> AppRegistry:
        return self.agent.app_registry

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self.client, name)

    async def __aenter__(self) -> "FreshApp":
        await self.client.__aenter__()
        await self.agent.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.agent.__aexit__(*exc)
        await self.client.__aexit__(*exc)

    def __enter__(self) -> "FreshApp":
        from koil import unkoil

        return unkoil(self.__aenter__)

    def __exit__(self, *exc: Any) -> None:
        from koil import unkoil

        unkoil(self.__aexit__, *exc)

    async def aconnect(
        self,
        context: Any | None = None,
        *,
        force: bool | None = None,
        timeout: float | None = None,
    ) -> None:
        import time as _time

        from rekuest.agents.errors import AgentException

        if force is not None:
            self.agent.force = force
        started = _time.monotonic()
        deadline = started + REGISTRATION_DRAIN_TIMEOUT
        attempt = 0
        while True:
            attempt += 1
            try:
                await self.agent.aconnect(context, timeout=timeout)
                if attempt > 1:
                    logger.info(
                        "Agent %s registered after %d attempts (%.1fs): the previous "
                        "test's registration took that long to drain",
                        self.agent.name,
                        attempt,
                        _time.monotonic() - started,
                    )
                return
            except AgentException as e:
                if "already registered" not in str(e) or _time.monotonic() > deadline:
                    raise
                await asyncio.sleep(0.5)

    async def aloop(self) -> None:
        await self.agent.aloop()


def build_fresh_rekuest(setup: Deployment, token: str = "test") -> FreshApp:
    """Build a brand-new ``Rekuest`` against an already-running deployment.

    Every call gets its own empty :class:`AppRegistry`, so registrations made by
    one test are completely invisible to the next. This is the per-test
    entrypoint for the integration tests: build the app, register functions on
    it, then run it against the shared docker stack.

    Args:
        setup: The running dokker deployment (from the ``deployment`` fixture).
        token: Static token to authenticate as. The deployment maps each
            configured token to a distinct ``client_app`` (see
            ``authentikate.static_tokens`` in
            ``tests/integration/configs/rekuest.yaml``), so passing different
            tokens to different ``build_fresh_rekuest`` calls makes the server
            treat them as genuinely separate apps. The server binds the agent to
            its instance based on this authentication. Defaults to ``"test"``.

    Returns:
        A fresh, not-yet-entered app: its client and the agent serving its registry.
    """
    loader = make_token_loader(token)
    port = setup.spec.find_service("rekuest").get_port_for_internal(80).published
    http_url = f"http://localhost:{port}/graphql"
    ws_url = f"ws://localhost:{port}/graphql"
    agi_url = f"ws://localhost:{port}/agi"

    rath = RekuestRath(
        link=compose(
            ComposedAuthLink(token_loader=loader, token_refresher=loader),
            SplitLink(
                left=AIOHttpLink(endpoint_url=http_url),
                right=GraphQLWSLink(ws_endpoint_url=ws_url),
                split=lambda o: o.node.operation != OperationType.SUBSCRIPTION,
            ),
        ),
    )

    agent = RekuestAgent(
        transport=WebsocketAgentTransport(
            endpoint_url=agi_url,
            token_loader=loader,
        ),
        name=f"Test-{token}-{uuid4().hex[:8]}",
        app_registry=AppRegistry(),
    )

    return FreshApp(
        client=Rekuest(
            rath=rath,
            postman=GraphQLPostman(rath=rath),
            structure_registry=agent.app_registry.structure_registry,
        ),
        agent=agent,
    )


@pytest_asyncio.fixture(scope="session")
async def deployment(integration_ports: dict[str, int]) -> AsyncGenerator[Deployment, None]:
    """Bring the rekuest stack up once per session and yield the dokker setup.

    Tests build their own fresh ``Rekuest`` (fresh ``AppRegistry``, unique
    instance id) against this running stack via :func:`build_fresh_rekuest`, so
    no registry state ever leaks between tests.
    """
    # `testing()` rather than `local()`: it gives every session its own compose
    # project, so concurrent runs cannot tear each other's stack down, and its
    # teardown policy already downs the project on exit. `local()` derives a
    # stable project name from the directory, which random host ports alone do
    # not make safe to share.
    setup = testing(compose_files)
    setup.add_health_check(
        url=lambda spec: (
            f"http://localhost:{spec.find_service('rekuest').get_port_for_internal(80).published}/graphql"
        ),
        service="rekuest",
        timeout=5,
        max_retries=10,
    )

    async with setup:
        await setup.adown()
        await setup.apull()
        await setup.aup()
        await setup.acheck_health()
        yield setup


@pytest_asyncio.fixture(scope="session")
async def async_deployed_app(
    deployment: Deployment,
) -> AsyncGenerator[DeployedRekuest, None]:
    """A deployed app with ``most_basic_function`` registered on a fresh registry.

    Built on top of the shared ``deployment`` fixture via
    :func:`build_fresh_rekuest`, so it too gets its own ``AppRegistry``.
    """
    watcher = deployment.create_watcher("rekuest")
    rustfs_watcher = deployment.create_watcher("rustfs")

    rekuest = build_fresh_rekuest(deployment)
    rekuest.registry.register(most_basic_function)

    async with rekuest as rekuest:
        deployed = DeployedRekuest(
            deployment=deployment,
            rekuest_watcher=watcher,
            rustfs_watcher=rustfs_watcher,
            rekuest=rekuest,
        )

        yield deployed
