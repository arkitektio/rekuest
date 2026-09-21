"""The remote-call engine takes everything it uses from its caller.

There is no current client, postman or parent task to fall back to: a
``Rekuest`` client (or a per-task view of one) passes its own.
"""

import pytest

import rekuest.client.remote as remote
from rekuest.api.schema import Action


def _patch(monkeypatch: pytest.MonkeyPatch, seen: list) -> None:
    async def fake_ashrink_args(action_obj, args, kwargs, structure_registry=None):  # noqa: ANN001, ANN202
        seen.append(("shrink", structure_registry))
        return {"value": 1}

    async def fake_acall_raw(**kwargs):  # noqa: ANN003, ANN202
        seen.append(("postman", kwargs["options"].postman))
        return ("raw",)

    async def fake_aiterate_raw(**kwargs):  # noqa: ANN003, ANN202
        yield ("raw",)

    async def fake_aexpand_returns(action_obj, returns, structure_registry=None):  # noqa: ANN001, ANN202
        assert returns == ("raw",)
        seen.append(("expand", structure_registry))
        return ("expanded",)

    monkeypatch.setattr(remote, "ashrink_args", fake_ashrink_args)
    monkeypatch.setattr(remote, "acall_raw", fake_acall_raw)
    monkeypatch.setattr(remote, "aiterate_raw", fake_aiterate_raw)
    monkeypatch.setattr(remote, "aexpand_returns", fake_aexpand_returns)


@pytest.mark.asyncio
async def test_aiterate_expands_raw_yield_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    action = Action.model_construct(id="action-1")
    seen: list = []
    _patch(monkeypatch, seen)
    registry = object()

    results = [
        item
        async for item in remote.aiterate(
            action, value=1, structure_registry=registry, postman=object()
        )
    ]

    assert results == ["expanded"]


@pytest.mark.asyncio
async def test_the_callers_registry_and_postman_are_the_ones_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = Action.model_construct(id="action-1")
    seen: list = []
    _patch(monkeypatch, seen)
    registry, postman = object(), object()

    assert await remote.acall(
        action, value=1, structure_registry=registry, postman=postman
    ) == "expanded"
    assert seen == [("shrink", registry), ("postman", postman), ("expand", registry)]


@pytest.mark.asyncio
async def test_without_a_registry_there_is_nothing_to_fall_back_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, [])
    with pytest.raises(ValueError, match="No structure registry"):
        await remote.acall(Action.model_construct(id="a"), postman=object())


@pytest.mark.asyncio
async def test_without_a_postman_there_is_nothing_to_fall_back_to() -> None:
    with pytest.raises(ValueError, match="No postman"):
        await remote.acall_raw(kwargs={}, action=Action.model_construct(id="a"))


def test_the_parent_is_only_ever_what_was_passed() -> None:
    assert remote._resolve_parent(None) is None
