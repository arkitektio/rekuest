"""The provider of an arkitekt app served by rekuest: the agent ``run(app)`` builds.

The clients (``Rekuest``, ``RekuestCaller``) declare themselves in
:mod:`rekuest.client.client` and the types rekuest sends by id in
:mod:`rekuest.structures`. An app served by this provider uses ``Rekuest`` whether or
not it names it.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from arkitekt_spec import AppManifest
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.provider import provider
from arkitekt_spec.declare.wiring import Alias, Require, TokenLoader
from fakts.contrib.rath.auth import FaktsAuthLink
from rath.links.aiohttp import AIOHttpLink
from rath.links.compose import compose

from rekuest.agents.agent import RekuestAgent
from rekuest.agents.transport.websocket import WebsocketAgentTransport
from rekuest.client.client import REKUEST, Rekuest
from rekuest.client.rath import RekuestRath
from rekuest.client.upload.assets import aupload_assets
from rekuest.datalayer import DataLayer


def _asset_upload(
    rekuest: Alias, s3: Alias, tokens: TokenLoader, registry: AppRegistry
) -> Callable[[], Awaitable[None]]:
    """What uploads the registry's assets: over a connection of its own, closed after."""

    async def upload() -> None:
        client = RekuestRath(
            link=compose(
                FaktsAuthLink(token_loader=tokens),
                AIOHttpLink(
                    endpoint_url=rekuest.to_http_path("graphql"),
                    proxy=rekuest.proxy,
                    ssl_context=rekuest.ssl_context(),
                ),
            )
        )
        async with client:
            await aupload_assets(
                client, DataLayer.from_alias(s3), registry.registered_assets.values()
            )

    return upload


@provider(clients=[Rekuest])
def rekuest_agent(
    rekuest: Annotated[Alias, REKUEST],
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
            if any(
                asset.path is not None for asset in registry.registered_assets.values()
            )
            else None
        ),
    )


rekuest_provider = rekuest_agent

__all__ = ["rekuest_provider"]
