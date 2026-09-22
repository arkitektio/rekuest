"""The rekuest service and provider of an arkitekt app, and the types rekuest sends by id.

Declared on one registry: the service first, then the provider that builds the
agent from the client it returns, then the structures whose expanders ask for
that client. An app takes all of it in with ``App(providers=[rekuest_provider])``
-- or implicitly, on its first offering.
"""

import os
from typing import Annotated

from fakts import Alias, Fakts, Require, TokenLoader
from fakts.contrib.rath.auth import FaktsAuthLink
from graphql import OperationType
from rath.links.aiohttp import AIOHttpLink
from rath.links.compose import compose
from rath.links.graphql_ws import GraphQLWSLink
from rath.links.split import SplitLink

from rekuest.agents.base import RekuestAgent
from rekuest.api.schema import (
    Action,
    Implementation,
    SearchActionsQuery,
    SearchImplementationsQuery,
    SearchShortcutsQuery,
    SearchTestCasesQuery,
    SearchTestResultsQuery,
    Shortcut,
    TaskEvent,
    TestCase,
    TestResult,
)
from rekuest.app import AppRegistry
from rekuest.agents.transport.websocket import WebsocketAgentTransport
from rekuest.datalayer import DataLayer
from rekuest.client.upload.link import UploadLink
from rekuest.client.postman import GraphQLPostman
from rekuest.client.rath import RekuestRath
from rekuest.client.client import Rekuest
from rekuest.widgets import SearchWidget

def build_relative_path(*path: str) -> str:
    """Build a path relative to this file, for the files shipped beside it."""
    return os.path.join(os.path.dirname(__file__), *path)


registry = AppRegistry()
"""What rekuest brings to an app: its service, and the types it can send by id."""


@registry.service(
    schema=build_relative_path("api", "schema.graphql"),
    turms=build_relative_path("api", "project.json"),
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
                left=AIOHttpLink(endpoint_url=rekuest.to_http_path("graphql")),
                right=GraphQLWSLink(ws_endpoint_url=rekuest.to_ws_path("graphql")),
                split=lambda o: o.node.operation != OperationType.SUBSCRIPTION,
            ),
        )
    )
    return Rekuest(
        rath=rath,
        postman=GraphQLPostman(rath=rath),
        structure_registry=registry.structure_registry,
    )


@registry.provider()
def rekuest_agent(
    rekuest: Annotated[
        Alias,
        Require(
            "live.arkitekt.rekuest", "Where this app's actions are offered and assigned"
        ),
    ],
    fakts: Fakts,
    registry: AppRegistry,
) -> RekuestAgent:
    """The agent that provides this app's actions to rekuest.

    Built by a run after its clients: it serves the run's ``registry`` over the
    agent socket of the resolved rekuest service, authenticating with the run's
    token, and registers under the app's manifest name. Everything it needs --
    registering, state, dependencies, the shelve -- goes over that socket, so it
    takes no client. The run binds it and drives it; nothing is started here.
    """
    return RekuestAgent(
        transport=WebsocketAgentTransport(
            endpoint_url=rekuest.to_ws_path("agi"),
            token_loader=fakts.aget_token,
        ),
        name=f"{fakts.manifest.identifier}:{fakts.manifest.version}",
        # What the app said it is, carried onto the agent: the name identifies
        # it, the description tells two of them apart.
        description=fakts.manifest.description,
        app_registry=registry,
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
