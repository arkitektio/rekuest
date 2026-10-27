"""The rekuest service and provider of an arkitekt app, and the types rekuest sends by id.

Declared on one registry: the service first, then the provider that builds the
agent from the client it returns, then the structures whose expanders ask for
that client. An app takes all of it in with ``App(providers=[rekuest_provider])``
-- or implicitly, on its first offering.
"""

import os
from collections.abc import Awaitable, Callable
from typing import Annotated

from arkitekt_spec import AppManifest
from fakts import Alias, Require, TokenLoader
from fakts.contrib.rath.auth import FaktsAuthLink
from graphql import OperationType
from rath.links.aiohttp import AIOHttpLink
from rath.links.compose import compose
from rath.links.graphql_ws import GraphQLWSLink
from rath.links.split import SplitLink

from rekuest.agents.agent import RekuestAgent
from rekuest.api.schema import (
    Action,
    Agent,
    DetailMaterializedBlok,
    Implementation,
    SearchActionsQuery,
    SearchAgentsQuery,
    SearchImplementationsQuery,
    SearchMaterializedBloksQuery,
    SearchShortcutsQuery,
    SearchTestCasesQuery,
    SearchTestResultsQuery,
    Shortcut,
    TaskEvent,
    TestCase,
    TestResult,
)
from arkitekt_spec.declare.app import AppRegistry
from rekuest.agents.transport.websocket import WebsocketAgentTransport
from rekuest.datalayer import DataLayer
from rekuest.client.upload.assets import aupload_assets
from rekuest.client.upload.link import UploadLink
from rekuest.client.postman import GraphQLPostman
from rekuest.client.rath import RekuestRath
from rekuest.client.client import Rekuest
from arkitekt_spec.declare.widgets import SearchWidget

def build_relative_path(*path: str) -> str:
    """Build a path relative to this file, for the files shipped beside it."""
    return os.path.join(os.path.dirname(__file__), *path)


registry = AppRegistry()
"""What rekuest brings to an app: its service, and the types it can send by id."""


@registry.service(
    schema=build_relative_path("api", "schema.graphql"),
    turms=build_relative_path("api", "project.json"),
    # The server this release of the client is written against: what a hub made for
    # an app's tests runs. Moves with the server's major, not with this package's.
    image="jhnnsrs/rekuest:11",
)
def rekuest(
    rekuest: Annotated[
        Alias,
        Require(
            "live.arkitekt.rekuest", "Where this app's actions are offered and assigned"
        ),
    ],
    s3: Annotated[
        Alias,
        Require("live.arkitekt.s3", "Where this app's uploads are stored"),
    ],
    tokens: TokenLoader,
    registry: AppRegistry,
) -> Rekuest:
    """Rekuest: call other apps' actions, and offer this app's through :func:`rekuest_agent`.

    Takes the run's ``registry`` because that is what its calls (de)serialize
    with. It builds no agent: the provider beside it does, and the run owns that
    agent. Anything that needs this API -- listing the deployment's actions,
    finding one, calling raw -- takes this client by annotation; the agent never
    does.
    """
    rath = RekuestRath(
        link=compose(
            UploadLink(datalayer=DataLayer.from_alias(s3)),
            FaktsAuthLink(token_loader=tokens),
            SplitLink(
                left=AIOHttpLink(endpoint_url=rekuest.to_http_path("graphql"), proxy=rekuest.proxy, ssl_context=rekuest.ssl_context()),
                right=GraphQLWSLink(ws_endpoint_url=rekuest.to_ws_path("graphql"), proxy=rekuest.proxy, ssl_context=rekuest.ssl_context()),
                split=lambda o: o.node.operation != OperationType.SUBSCRIPTION,
            ),
        )
    )
    return Rekuest(
        rath=rath,
        postman=GraphQLPostman(rath=rath),
        structure_registry=registry.structure_registry,
    )


def _asset_upload(
    rekuest: Alias, s3: Alias, tokens: TokenLoader, registry: AppRegistry
) -> Callable[[], Awaitable[None]]:
    """What uploads the registry's assets: over a connection of its own, closed after."""

    async def upload() -> None:
        client = RekuestRath(
            link=compose(
                FaktsAuthLink(token_loader=tokens),
                AIOHttpLink(endpoint_url=rekuest.to_http_path("graphql"), proxy=rekuest.proxy, ssl_context=rekuest.ssl_context()),
            )
        )
        async with client:
            await aupload_assets(
                client, DataLayer.from_alias(s3), registry.registered_assets.values()
            )

    return upload


@registry.provider()
def rekuest_agent(
    rekuest: Annotated[
        Alias,
        Require(
            "live.arkitekt.rekuest", "Where this app's actions are offered and assigned"
        ),
    ],
    s3: Annotated[
        Alias,
        Require("live.arkitekt.s3", "Where this app's assets are stored"),
    ],
    tokens: TokenLoader,
    manifest: AppManifest,
    registry: AppRegistry,
) -> RekuestAgent:
    """The agent that provides this app's actions to rekuest.

    Built by a run after its clients: it serves the run's ``registry`` over the
    agent socket of the resolved rekuest service, authenticating with the run's
    token, and registers under the app's manifest name. Everything it needs --
    registering, state, dependencies, the shelve -- goes over that socket, so it
    takes no client. The run binds it and drives it; nothing is started here.

    The files the app registered as assets are the one thing that does not fit a
    socket frame: once the server accepted the registration, what it does not hold
    of them is uploaded over a connection made for that and closed after.
    """
    return RekuestAgent(
        transport=WebsocketAgentTransport(
            endpoint_url=rekuest.to_ws_path("agi"),
            proxy=rekuest.proxy,
            ssl_context=rekuest.ssl_context(),
            token_loader=tokens.aget_token,
        ),
        name=f"{manifest.identifier}:{manifest.version}",
        # What the app said it is, carried onto the agent: the name identifies
        # it, the description tells two of them apart.
        description=manifest.description,
        app_registry=registry,
        after_registration=(
            _asset_upload(rekuest, s3, tokens, registry)
            if any(asset.path is not None for asset in registry.registered_assets.values())
            else None
        ),
    )


def _search(query: object) -> SearchWidget:
    """The widget that picks one of these out of the deployment."""
    return SearchWidget(query=query.Meta.document, ward="rekuest")  # type: ignore[attr-defined]


@registry.structure(
    "@rekuest/implementation", widget=_search(SearchImplementationsQuery)
)
async def expand_implementation(id: str, rekuest: Rekuest) -> Implementation:
    """An implementation, by id."""
    return await rekuest.aget_implementation(id)


@registry.structure("@rekuest/action", widget=_search(SearchActionsQuery))
async def expand_action(id: str, rekuest: Rekuest) -> Action:
    """An action, by id.

    ``find`` is a multi-argument finder -- ``find(id, implementation, hash,
    matching)``, all optional -- so the id is passed by name rather than relying
    on it happening to be first.
    """
    return await rekuest.afind(id=id)


@registry.structure("@rekuest/shortcut", widget=_search(SearchShortcutsQuery))
async def expand_shortcut(id: str, rekuest: Rekuest) -> Shortcut:
    """A shortcut, by id."""
    return await rekuest.aget_shortcut(id)


@registry.structure("@rekuest/testcase", widget=_search(SearchTestCasesQuery))
async def expand_test_case(id: str, rekuest: Rekuest) -> TestCase:
    """A test case, by id."""
    return await rekuest.aget_test_case(id)


@registry.structure("@rekuest/testresult", widget=_search(SearchTestResultsQuery))
async def expand_test_result(id: str, rekuest: Rekuest) -> TestResult:
    """A test result, by id."""
    return await rekuest.aget_test_result(id)


@registry.structure(
    "@rekuest/materialized_blok", widget=_search(SearchMaterializedBloksQuery)
)
async def expand_materialized_blok(id: str, rekuest: Rekuest) -> DetailMaterializedBlok:
    """A materialized blok, by id, with the blok it shows: its tree, demo state and dependencies.

    The identifier is the one orkestrator already displays and links
    (``rekuest/manifest.ts``), so a message carrying one renders as the blok.
    """
    return await rekuest.aget_materialized_blok(id)


SUPERVISOR = "@rekuest/supervisor"
"""The descriptor of ``@rekuest/agent``: the app the agent says controls its lifecycle.

Self-declared (``ARKITEKT_SUPERVISOR``), so a supervisor's port narrows its picker with it
(``Requires(SUPERVISOR, DescriptorOperator.EQUALS, "plugin_engine")``) and still maps the
agent to its own records before acting: it is a hint, never authority."""


def describe_agent(agent: Agent) -> dict[str, str]:
    """An agent's descriptors: the supervisor it declared, if any.

    None declared is no descriptor at all, not ``None``: a constraint on a key the object
    does not carry is not tested, so an app that predates ``ARKITEKT_SUPERVISOR`` still
    reaches its supervisor's actions (which decide by their own records anyway).
    """
    return {SUPERVISOR: agent.supervisor} if agent.supervisor else {}


@registry.structure("@rekuest/agent", widget=_search(SearchAgentsQuery), describe=describe_agent)
async def expand_agent(id: str, rekuest: Rekuest) -> Agent:
    """An agent, by id: an app instance connected to rekuest, with its lok client id."""
    return await rekuest.aget_agent(id)


@registry.structure("@rekuest/taskevent")
async def expand_task_event(id: str, rekuest: Rekuest) -> TaskEvent:
    """A task event, by id. Its query is ``get_event``."""
    return await rekuest.aget_event(id)


# The service is named after its function ("rekuest": what the app's clients are
# keyed by and what its structure expanders are bound to); these are the names the
# package exports them under, for `App(services=[rekuest_service])` and the run.
rekuest_service = rekuest
rekuest_provider = rekuest_agent

__all__ = ["registry", "rekuest_service", "rekuest_provider"]
