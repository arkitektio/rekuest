"""The app's description reaches the agent that provides it.

``App(description=...)`` writes the description into the fakts manifest, and
``rekuest.arkitekt.rekuest_provider`` is the single place that turns a manifest
into a :class:`~rekuest.agents.base.RekuestAgent`. That one line is the whole
seam between the two packages, and everything else about descriptions is
testable without it -- which is exactly why it is worth pinning here: a typo in
``fakts.manifest.description`` would leave every other test passing and simply
never show a description in the UI.
"""

from typing import Any

import pytest
from fakts import Alias
from fakts.models import Manifest

from rekuest.app import AppRegistry
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
