"""A call runs only if the agent's state holds what it requires, end to end.

The provider publishes a stage. A root call says what the stage must hold; the server
checks that the state and its member exist and that the agent tests requirements, carries
them to the agent, and the agent tests them with the stage's lock held. A mismatch comes
back as ``StatePreconditionFailed`` and nothing of the action ran.
"""

import asyncio
from typing import Any

import pytest
from dokker import Deployment

from arkitekt_runtime.postmans.errors import PostmanException
from arkitekt_spec.declare.errors import StatePreconditionFailed
from arkitekt_spec.declare.task import StateRequirement

from .conftest import CONNECT_TIMEOUT, build_fresh_rekuest

ran: list[str] = []


def _provide_a_stage(provider: Any) -> None:  # noqa: ANN401
    registry = provider.agent.app_registry

    @registry.state(name="stage", required_locks=["stage"])
    class Stage:
        """A stage others drive."""

        busy: bool = False
        mode: str = "idle"

    def home() -> Stage:
        """Home the stage."""
        return Stage()

    def move(stage: Stage, mode: str) -> str:
        """Switch the stage's mode."""
        ran.append(mode)
        stage.mode = mode
        return stage.mode

    provider.startup(home)
    registry.register(move, locks=["stage"])


def _mode_is(mode: str) -> StateRequirement:
    return StateRequirement(state="stage", path="/mode", operator="EQUALS", value=mode)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_call_runs_only_while_the_state_holds_what_it_requires(deployment: Deployment) -> None:
    ran.clear()
    provider = build_fresh_rekuest(deployment, token="atest_token")
    _provide_a_stage(provider)
    async with provider as provider:
        await provider.aconnect(timeout=CONNECT_TIMEOUT)
        loop = asyncio.create_task(provider.aloop())
        try:
            move = await provider.amy_implementation_at("move")

            assert await provider.acall(move, "fast", state_requires=[_mode_is("idle")]) == "fast"

            # The stage is fast now: a call that wants it idle is refused before it runs.
            with pytest.raises(StatePreconditionFailed, match=r"stage/mode EQUALS 'idle' \(actual 'fast'\)"):
                await provider.acall(move, "slow", state_requires=[_mode_is("idle")])
            assert ran == ["fast"]

            # A member the state does not declare is refused by the server, never sent.
            with pytest.raises(PostmanException, match="declares no"):
                await provider.acall(
                    move,
                    "slow",
                    state_requires=[StateRequirement(state="stage", path="/speed", operator="EQUALS", value=1)],
                )

            # Without requirements, the call runs as always.
            assert await provider.acall(move, "slow") == "slow"
            assert ran == ["fast", "slow"]
        finally:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)
