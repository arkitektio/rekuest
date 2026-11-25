"""The app's description reaches the agent that provides it.

``rekuest.arkitekt.rekuest_provider`` is the single place that turns an app's
manifest into a :class:`~rekuest.agents.agent.RekuestAgent`. A typo in
``manifest.description`` there would leave every other test passing and simply
never show a description in the UI, so the seam is pinned here.
"""

from pathlib import Path

import pytest
from arkitekt_spec import AppManifest
from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.wiring import Alias

from rekuest.agents.agent import RekuestAgent
from rekuest.arkitekt import rekuest_provider


class StubTokens:
    """A token loader handing out one token."""

    async def aget_token(self) -> str:
        return "token"

    async def arefresh_token(self, stale_token: str | None = None) -> str:
        return "token"


def _alias(name: str) -> Alias:
    return Alias(id=name, host="localhost", port=80, path=name)


def _agent(
    description: str | None,
    registry: AppRegistry | None = None,
    rekuest: Alias | None = None,
) -> RekuestAgent:
    return rekuest_provider.function(
        rekuest=rekuest or _alias("rekuest"),
        s3=_alias("s3"),
        tokens=StubTokens(),
        manifest=AppManifest(
            identifier="com.test.described",
            version="1.2.3",
            description=description,
            scopes=["openid"],
        ),
        registry=registry or AppRegistry(),
    )


def test_the_agent_takes_its_description_from_the_manifest() -> None:
    agent = _agent("What this app is, in a sentence.")

    # The name identifies the agent; the description is what tells two of them
    # apart, so both have to survive the trip from the manifest.
    assert agent.name == "com.test.described:1.2.3"
    assert agent.description == "What this app is, in a sentence."


def test_an_app_without_one_gives_the_agent_none() -> None:
    """Not the empty string: the backend keeps what it has when none is sent."""
    agent = _agent(None)

    assert agent.name == "com.test.described:1.2.3"
    assert agent.description is None


@pytest.mark.asyncio
async def test_the_agent_socket_goes_through_the_alias_proxy_with_the_run_tokens() -> (
    None
):
    mesh = Alias(id="rekuest", host="100.64.0.9", port=8000, kind="mesh")
    agent = _agent(None, rekuest=mesh.through_mesh("http://127.0.0.1:41234"))

    transport = agent.transport
    assert transport.endpoint_url == "ws://100.64.0.9:8000/agi"
    assert transport.proxy == "http://127.0.0.1:41234"
    assert await transport.token_loader() == "token"


def test_only_an_app_with_assets_gets_something_to_do_after_registering(
    tmp_path: Path,
) -> None:
    assert _agent(None).after_registration is None

    # A file the app does not hold is fetched by an admin's client: nothing to upload.
    registry = AppRegistry(root=tmp_path)
    registry.register_asset(
        "robot", source="https://vendor.example/ur5.glb", ref="sha256:" + "9f" * 32
    )
    assert _agent(None, registry).after_registration is None

    (tmp_path / "scope.glb").write_bytes(b"glTF\x02\x00\x00\x00")
    registry.register_asset("scope", "scope.glb")

    assert _agent(None, registry).after_registration is not None
