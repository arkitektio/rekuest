"""What a call names as its target reaches the postman as an id.

The translation happens once, at the boundary between the two halves of the call
surface: ``rekuest.client.remote`` holds the fetched ``Action``/``Implementation`` and
reads ``.id`` off it, ``rekuest.calls`` only ever sees the id. Every other test
that drives these helpers leaves the target unset, so both sides of that
boundary were unguarded.
"""

from collections.abc import AsyncGenerator, Sequence
from typing import Any

import pytest

from rekuest.api.schema import TaskEventKind


class RecordingPostman:
    """Records the one ``aassign`` it is given, then completes the task."""

    connected = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def aassign(self, **kwargs: Any) -> AsyncGenerator[Any, None]:  # noqa: ANN401
        self.calls.append(kwargs)
        yield _Event(TaskEventKind.YIELD, returns=("ok",))
        yield _Event(TaskEventKind.COMPLETED)


class _Event:
    """As much of a task event as the stream helper reads."""

    def __init__(self, kind: TaskEventKind, returns: Sequence[Any] = ()) -> None:
        self.kind = kind
        self.returns = returns
        self.message = None


class _Target:
    """An ``Action`` or ``Implementation``, as far as the translation goes."""

    def __init__(self, id: str) -> None:
        self.id = id


# --------------------------------------------------------------------------- #
# The agnostic half: an id goes straight through
# --------------------------------------------------------------------------- #


async def _stream(postman: RecordingPostman, **kwargs: Any) -> list[Any]:  # noqa: ANN401
    from rekuest.calls import _astream_raw

    return [item async for item in _astream_raw(postman, **kwargs)]


@pytest.mark.asyncio
async def test_an_action_id_goes_to_the_postman_unchanged() -> None:
    postman = RecordingPostman()

    assert await _stream(postman, action_id="action-1") == [("ok",)]

    (call,) = postman.calls
    assert (call["action"], call["implementation"]) == ("action-1", None)


@pytest.mark.asyncio
async def test_an_implementation_id_goes_to_the_postman_unchanged() -> None:
    postman = RecordingPostman()

    await _stream(postman, implementation_id="impl-1")

    (call,) = postman.calls
    assert (call["action"], call["implementation"]) == (None, "impl-1")


@pytest.mark.asyncio
async def test_a_call_with_no_target_sends_neither_id() -> None:
    """A dependency or method call names no target at all."""
    postman = RecordingPostman()

    await _stream(postman, method="a_method")

    (call,) = postman.calls
    assert (call["action"], call["implementation"]) == (None, None)
    assert call["method"] == "a_method"


# --------------------------------------------------------------------------- #
# The client half: a fetched model is reduced to its id, and only here
# --------------------------------------------------------------------------- #


async def _iterate(postman: RecordingPostman, **kwargs: Any) -> list[Any]:  # noqa: ANN401
    from rekuest.client.remote import aiterate_raw

    return [item async for item in aiterate_raw(postman=postman, **kwargs)]


@pytest.mark.asyncio
async def test_a_fetched_action_is_reduced_to_its_id() -> None:
    postman = RecordingPostman()

    assert await _iterate(postman, action=_Target("action-1")) == [("ok",)]

    (call,) = postman.calls
    assert (call["action"], call["implementation"]) == ("action-1", None)


@pytest.mark.asyncio
async def test_a_fetched_implementation_is_reduced_to_its_id() -> None:
    postman = RecordingPostman()

    await _iterate(postman, implementation=_Target("impl-1"))

    (call,) = postman.calls
    assert (call["action"], call["implementation"]) == (None, "impl-1")
