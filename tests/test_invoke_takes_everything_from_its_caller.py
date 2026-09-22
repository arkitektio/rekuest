"""The call engine takes everything it uses from its caller.

There is no current client, postman or registry to fall back to. A ``Rekuest`` passes its
own and makes a root; a ``Task`` passes its agent's and makes a child. Both are required
arguments, so omitting one is a ``TypeError`` at the call, not a ``ValueError`` deep inside.
"""

import inspect

import pytest

import rekuest.invoke as invoke
from rekuest.api.schema import Action


def _patch(monkeypatch: pytest.MonkeyPatch, seen: list) -> None:
    async def fake_ashrink_args(action_obj, args, kwargs, structure_registry=None):  # noqa: ANN001, ANN202
        seen.append(("shrink", structure_registry))
        return {"value": 1}

    async def fake_acall_raw(**kwargs):  # noqa: ANN003, ANN202
        seen.append(("postman", kwargs["postman"]))
        return ("raw",)

    async def fake_aiterate_raw(**kwargs):  # noqa: ANN003, ANN202
        seen.append(("postman", kwargs["postman"]))
        yield ("raw",)

    async def fake_aexpand_returns(action_obj, returns, structure_registry=None):  # noqa: ANN001, ANN202
        assert returns == ("raw",)
        seen.append(("expand", structure_registry))
        return ("expanded",)

    monkeypatch.setattr(invoke, "ashrink_args", fake_ashrink_args)
    monkeypatch.setattr(invoke, "_acall_raw", fake_acall_raw)
    monkeypatch.setattr(invoke, "_aiterate_raw", fake_aiterate_raw)
    monkeypatch.setattr(invoke, "aexpand_returns", fake_aexpand_returns)


@pytest.mark.asyncio
async def test_a_call_serializes_with_the_registry_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both halves of the round trip use the caller's registry, and nothing else."""
    seen: list = []
    _patch(monkeypatch, seen)
    registry, postman = object(), object()

    result = await invoke._acall(
        Action.model_construct(id="action-1"),
        postman=postman,
        structure_registry=registry,
    )

    assert result == "expanded"
    assert seen == [("shrink", registry), ("postman", postman), ("expand", registry)]


@pytest.mark.asyncio
async def test_a_stream_expands_every_raw_yield(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    _patch(monkeypatch, seen)
    registry, postman = object(), object()

    results = [
        item
        async for item in invoke._aiterate(
            Action.model_construct(id="action-1"),
            postman=postman,
            structure_registry=registry,
        )
    ]

    assert results == ["expanded"]
    assert seen == [("shrink", registry), ("postman", postman), ("expand", registry)]


# --------------------------------------------------------------------------- #
# What used to be a deferred ValueError is now a signature error
# --------------------------------------------------------------------------- #


def test_a_postman_is_not_optional() -> None:
    """Formerly ``postman: Postman | None = None`` plus a resolver that only raised.

    The failure moved from a ``ValueError`` at run time to a ``TypeError`` at the call --
    which is the entire point, so assert it rather than deleting the old test.
    """
    with pytest.raises(TypeError, match="postman"):
        invoke._acall(Action.model_construct(id="a"), structure_registry=object())  # type: ignore[call-arg]


def test_a_structure_registry_is_not_optional() -> None:
    with pytest.raises(TypeError, match="structure_registry"):
        invoke._acall(Action.model_construct(id="a"), postman=object())  # type: ignore[call-arg]


@pytest.mark.parametrize("dead", ["cached", "log", "options", "task"])
def test_the_dead_options_are_gone(dead: str) -> None:
    """``cached`` and ``log`` were accepted and then ``del``'d on the first line of four
    bodies; ``options``/``task`` were the merge layer that made the resolvers necessary.
    """
    for fn in (invoke._acall, invoke._aiterate, invoke._acall_raw, invoke._aiterate_raw):
        assert dead not in inspect.signature(fn).parameters, f"{fn.__name__}: {dead}"


def test_the_module_it_replaced_is_gone() -> None:
    """So that nothing quietly resurrects the optional-postman surface."""
    with pytest.raises(ModuleNotFoundError):
        import rekuest.client.remote  # noqa: F401


def test_the_parent_is_only_ever_what_was_passed() -> None:
    """Unchanged, and still true: there is no ambient parent to discover."""
    from rekuest.calls import _resolve_parent

    assert _resolve_parent(None) is None
