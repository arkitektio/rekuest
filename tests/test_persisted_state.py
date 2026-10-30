"""``@app.state(persist=...)``: a startup hook of the next run gets the last value back."""

from pathlib import Path

import pytest
from arkitekt_runtime.local import MemoryAgentTransport
from arkitekt_spec.declare.app import AppRegistry

from rekuest.agents.agent import RekuestAgent

from .test_journal_retention import _until


def _agent(path: Path, persist: object, *, with_gain: bool = False) -> tuple[RekuestAgent, list]:
    """An agent of an app with one persisted state, and what its startup hook was given."""
    registry = AppRegistry()

    if with_gain:

        @registry.state(name="camera", persist=persist)  # type: ignore[arg-type]
        class Camera:
            exposure: int = 1
            gain: int = 0

    else:

        @registry.state(name="camera", persist=persist)  # type: ignore[arg-type]
        class Camera:  # type: ignore[no-redef]
            exposure: int = 1

    @registry.state(name="other_camera")
    class Unpersisted:
        exposure: int = 1

    seen: list = []

    @registry.startup
    async def start(old: Camera | None) -> tuple[Camera, Unpersisted]:
        seen.append(old)
        return Camera(exposure=1 if old is None else old.exposure), Unpersisted()

    agent = RekuestAgent(
        name="persist",
        transport=MemoryAgentTransport(),
        app_registry=registry,
        journal_path=str(path / "journal.db"),
    )
    return agent, seen


def _stored(agent: RekuestAgent) -> str:
    assert agent._state_store is not None
    state = agent._state_store.load().get("camera")  # a periodic state: not before its first write
    return "" if state is None else state.value


@pytest.mark.asyncio
async def test_a_synchronous_state_is_on_disk_at_once_and_comes_back(tmp_path: Path) -> None:
    first, seen = _agent(tmp_path, "synchronous")
    await first.astart()
    await first.aactivate()
    assert seen == [None], "a first run has no old state"

    first.states["camera"].exposure = 5
    # Before any teardown: this is all a power cut leaves.
    await _until(lambda: '"exposure": 5' in _stored(first))
    assert set(first._state_store.load()) == {"camera"}, "only what opted in"  # type: ignore[union-attr]
    await first.atear_down()

    second, seen = _agent(tmp_path, "synchronous")
    await second.astart()
    await second.aactivate()
    assert [old.exposure for old in seen] == [5]
    await second.atear_down()


@pytest.mark.asyncio
async def test_a_periodic_state_is_flushed_and_changed_ports_read_as_none(tmp_path: Path) -> None:
    first, _ = _agent(tmp_path, 0.01)
    await first.astart()
    await first.aactivate()
    first.states["camera"].exposure = 7
    await _until(lambda: first._state_store is not None and '"exposure": 7' in _stored(first))
    await first.atear_down()

    changed, seen = _agent(tmp_path, 0.01, with_gain=True)
    await changed.astart()
    await changed.aactivate()
    assert seen == [None], "a value of other ports is not handed on"
    await changed.atear_down()
