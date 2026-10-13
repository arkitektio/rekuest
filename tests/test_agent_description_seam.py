"""The app's description reaches the agent that provides it.

``App(description=...)`` writes the description into the fakts manifest, and
``rekuest.arkitekt.rekuest_provider`` is the single place that turns a manifest
into a :class:`~rekuest.agents.base.RekuestAgent`. That one line is the whole
seam between the two packages, and everything else about descriptions is
testable without it -- which is exactly why it is worth pinning here: a typo in
``manifest.description`` would leave every other test passing and simply
never show a description in the UI.
"""

from typing import Any

import pytest
from fakts import Alias
from fakts.models import Manifest

from arkitekt_spec.declare.app import AppRegistry
from rekuest.arkitekt import rekuest_provider


class StubFakts:
    """The little of a ``Fakts`` the rekuest provider actually reads."""

    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest

    async def aget_alias(self, name: str) -> Alias:
        return Alias(id=name, host="localhost", port=80, path="rekuest")

    async def aget_token(self) -> str:
        return "token"


def _manifest(description: str | None) -> Manifest:
    return Manifest(
        identifier="com.test.described",
        version="1.2.3",
        description=description,
        scopes=["openid"],
    )


async def _agent(description: str | None) -> Any:
    return await rekuest_provider.build(
        StubFakts(_manifest(description)), AppRegistry(), {}
    )


@pytest.mark.asyncio
async def test_the_agent_takes_its_description_from_the_manifest() -> None:
    agent = await _agent("What this app is, in a sentence.")

    # The name identifies the agent; the description is what tells two of them
    # apart, so both have to survive the trip from the manifest.
    assert agent.name == "com.test.described:1.2.3"
    assert agent.description == "What this app is, in a sentence."


@pytest.mark.asyncio
async def test_an_app_without_one_gives_the_agent_none() -> None:
    """Not the empty string: the backend keeps what it has when none is sent."""
    agent = await _agent(None)

    assert agent.name == "com.test.described:1.2.3"
    assert agent.description is None


class MeshStubFakts(StubFakts):
    """Resolves rekuest to a mesh alias, as fakts does with the mesh on."""

    async def aget_alias(self, name: str) -> Alias:
        alias = Alias(id=name, host="100.64.0.9", port=8000, kind="mesh")
        return alias.through_mesh("http://127.0.0.1:41234")


@pytest.mark.asyncio
async def test_the_agent_socket_goes_through_the_alias_proxy_with_the_run_tokens() -> None:
    fakts = MeshStubFakts(_manifest(None))
    agent = await rekuest_provider.build(fakts, AppRegistry(), {})

    transport = agent.transport
    assert transport.endpoint_url == "ws://100.64.0.9:8000/agi"
    assert transport.proxy == "http://127.0.0.1:41234"
    assert await transport.token_loader() == "token"


@pytest.mark.asyncio
async def test_only_an_app_with_assets_gets_something_to_do_after_registering(tmp_path) -> None:
    assert (await _agent(None)).after_registration is None

    # A file the app does not hold is fetched by an admin's client: nothing to upload.
    registry = AppRegistry(root=tmp_path)
    registry.register_asset("robot", source="https://vendor.example/ur5.glb", ref="sha256:" + "9f" * 32)
    assert (await rekuest_provider.build(StubFakts(_manifest(None)), registry, {})).after_registration is None

    (tmp_path / "scope.glb").write_bytes(b"glTF\x02\x00\x00\x00")
    registry.register_asset("scope", "scope.glb")
    agent = await rekuest_provider.build(StubFakts(_manifest(None)), registry, {})

    assert agent.after_registration is not None
