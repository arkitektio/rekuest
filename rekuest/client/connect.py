"""How the rekuest clients reach a resolved deployment: the links and the caller session."""

from arkitekt_spec.declare.wiring import Alias, TokenLoader
from fakts.contrib.rath.auth import FaktsAuthLink
from rath.links.aiohttp import AIOHttpLink
from rath.links.compose import compose
from rath.links.graphql_ws import GraphQLWSLink
from rath.links.split import SplitLink

from graphql import OperationType
from rekuest.client.rath import RekuestRath
from rekuest.client.session import CallerSession
from rekuest.client.session_postman import SessionPostman
from rekuest.client.upload.link import UploadLink
from rekuest.datalayer import DataLayer


def rekuest_rath(rekuest: Alias, s3: Alias, tokens: TokenLoader) -> RekuestRath:
    """The connection both clients talk over: uploads, the run's token, http and ws."""
    return RekuestRath(
        link=compose(
            UploadLink(datalayer=DataLayer.from_alias(s3)),
            FaktsAuthLink(token_loader=tokens),
            SplitLink(
                left=AIOHttpLink(
                    endpoint_url=rekuest.to_http_path("graphql"),
                    proxy=rekuest.proxy,
                    ssl_context=rekuest.ssl_context(),
                ),
                right=GraphQLWSLink(
                    ws_endpoint_url=rekuest.to_ws_path("graphql"),
                    proxy=rekuest.proxy,
                    ssl_context=rekuest.ssl_context(),
                ),
                split=lambda o: o.node.operation != OperationType.SUBSCRIPTION,
            ),
        )
    )


def caller_postman(rekuest: Alias, tokens: TokenLoader) -> SessionPostman:
    """Calls over a caller session on takt: gap-free events, resumed after a drop."""
    return SessionPostman(
        session=CallerSession(
            rekuest.to_ws_path("session"),
            tokens.aget_token,
            ssl_context=rekuest.ssl_context(),
            proxy=rekuest.proxy,
            client_info={"app": "rekuest-python"},
        )
    )


__all__ = ["caller_postman", "rekuest_rath"]
