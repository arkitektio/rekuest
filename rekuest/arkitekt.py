"""ArkitektNextRekuestNext class."""

import json
import os
from typing import TYPE_CHECKING, Any
from rath.links.split import SplitLink
from fakts.contrib.rath.aiohttp import FaktsAIOHttpLink
from fakts.contrib.rath.graphql_ws import FaktsGraphQLWSLink
from fakts.contrib.rath.auth import FaktsAuthLink
from rekuest.contrib.arkitekt.datalayer import FaktsDataLayer
from rekuest.rath import RekuestNextRath
from rekuest.rekuest import RekuestNext
from graphql import OperationType
from rekuest.contrib.arkitekt.websocket_agent_transport import (
    ArkitektWebsocketAgentTransport,
)
from rekuest.agents.base import RekuestAgent
from fakts import Fakts
from rekuest.postmans.graphql import GraphQLPostman
from rekuest.links.upload import UploadLink
from .structures.default import get_default_structure_registry
from fakts.models import Requirement
from arkitekt.service_registry import Params, BaseArkitektService
from rath.links.compose import compose
from arkitekt.service_registry import (
    get_default_service_registry,
)


if TYPE_CHECKING:
    pass


def build_relative_path(*path: str) -> str:
    """Build a relative path to the current file."""
    return os.path.join(os.path.dirname(__file__), *path)


class RekuestNextService(BaseArkitektService):
    """Service for RekuestNext."""

    def __init__(self) -> None:
        """Initialize the RekuestNextService."""
        self.structure_reg = get_default_structure_registry()

    def get_service_name(self) -> str:
        """Get the service name."""
        return "rekuest"

    def build_service(self, fakts: Fakts, params: Params) -> "RekuestNext":
        """Build the service."""
        force = params.get("force", False)
        datalayer = FaktsDataLayer(fakts_group="s3", fakts=fakts)

        rath = RekuestNextRath(
            link=compose(
                UploadLink(
                    datalayer=datalayer,
                ),
                FaktsAuthLink(
                    fakts=fakts,
                ),
                SplitLink(
                    left=FaktsAIOHttpLink(
                        fakts_group="rekuest",
                        fakts=fakts,
                        endpoint_url="FAKE_URL",
                    ),
                    right=FaktsGraphQLWSLink(
                        fakts_group="rekuest",
                        fakts=fakts,
                        ws_endpoint_url="FAKE_URL",
                    ),
                    split=lambda o: o.node.operation != OperationType.SUBSCRIPTION,
                ),
            )
        )

        agent = RekuestAgent(
            transport=ArkitektWebsocketAgentTransport(
                fakts_group="rekuest",
                fakts=fakts,
                endpoint_url="FAKE_URL",
                token_loader=fakts.aget_token,
                force=force,
            ),
            rath=rath,
            name=f"{fakts.manifest.identifier}:{fakts.manifest.version}",
        )

        return RekuestNext(
            rath=rath,
            agent=agent,
            postman=GraphQLPostman(
                rath=rath,
            ),
        )

    def get_requirements(self) -> list[Requirement]:
        """Get the requirements for this service."""
        return [
            Requirement(
                key="rekuest",
                service="live.arkitekt.rekuest",
                description="An instance of ArkitektNext Rekuest to assign to actions",
            ),
            Requirement(
                key="s3",
                service="live.arkitekt.s3",
                description="An instance of ArkitektNext Rekuest to assign to actions",
            ),
        ]

    def get_graphql_schema(self) -> str:
        """Get the GraphQL schema for this service."""
        schema_graphql_path = build_relative_path("api", "schema.graphql")
        with open(schema_graphql_path) as f:
            return f.read()

    def get_turms_project(self) -> dict[str, Any]:
        """Get the turms project for this service."""
        turms_prject = build_relative_path("api", "project.json")
        with open(turms_prject) as f:
            return json.loads(f.read())


get_default_service_registry().register(RekuestNextService())
