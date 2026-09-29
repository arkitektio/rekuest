"""The canonical agent-socket frames (``fixtures/agent_wire.json``, a byte-for-byte copy
of the server's ``tests/fixtures/agent_wire.json``), as this package's wire sees them.

Every frame from the agent parses with the message models and dumps back to itself;
what the websocket transport would put on the wire is the fixture (plus nulls for unset
optional fields); every frame to the agent parses the way the transport parses what it
receives.
"""

import json
from pathlib import Path
from typing import Annotated, Any

import pytest
from arkitekt_runtime import messages
from pydantic import Field, TypeAdapter

from rekuest.agents.transport.websocket import InMessagePayload

WIRE = json.loads((Path(__file__).parent / "fixtures" / "agent_wire.json").read_text())

FROM_AGENT: TypeAdapter[messages.FromAgentMessage] = TypeAdapter(
    Annotated[messages.FromAgentMessage, Field(discriminator="type")]
)


def _cases(*groups: str) -> list[Any]:
    return [
        pytest.param(case["frame"], id=f"{group}:{case['name']}")
        for group in groups
        for case in WIRE[group]
    ]


@pytest.mark.parametrize(
    "frame", _cases("numbered_from_agent", "unnumbered_from_agent")
)
def test_every_frame_from_the_agent_round_trips(frame: dict[str, Any]) -> None:
    message = FROM_AGENT.validate_python(frame)
    assert message.model_dump(mode="json", by_alias=True, exclude_unset=True) == frame


@pytest.mark.parametrize("frame", _cases("numbered_from_agent"))
def test_numbered_frames_are_what_the_transport_sends(frame: dict[str, Any]) -> None:
    message = FROM_AGENT.validate_python(frame)
    assert message.model_dump(mode="json", by_alias=True, exclude_none=True) == frame
    # The transport sends ``model_dump_json()``: unset optional fields go as null.
    sent = json.loads(message.model_dump_json())
    assert {k: v for k, v in sent.items() if v is not None} == frame
    # An unset stamp is left out, never sent as null.
    assert ("task_step" in sent) == ("task_step" in frame)
    assert {"pos", "journal_session", "agent_ts"} <= set(sent)


@pytest.mark.parametrize("frame", _cases("to_agent"))
def test_every_frame_to_the_agent_parses_as_the_transport_receives_it(
    frame: dict[str, Any],
) -> None:
    message = InMessagePayload(message=json.loads(json.dumps(frame))).message
    assert message.model_dump(mode="json", by_alias=True, exclude_none=True) == frame
