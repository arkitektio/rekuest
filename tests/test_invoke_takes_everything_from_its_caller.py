"""The call engine takes everything it uses from its caller.

There is no current client or postman to fall back to. A ``Rekuest`` passes its own and
makes a root; a ``Task`` passes its agent's and makes a child. The postman is required, so
omitting it is a ``TypeError`` at the call. The target is found at run time, so it is
called by reference: no registry, and results expand through the caller's run (``origin``).
"""

import inspect
from typing import Any, cast

import arkitekt_runtime.invoke as invoke
import pytest

from rekuest.api.schema import Action


def _patch(monkeypatch: pytest.MonkeyPatch, seen: list) -> None:
    async def fake_ashrink_args(action_obj, args, kwargs, structure_registry):  # noqa: ANN001, ANN202
        seen.append(("shrink", structure_registry))
        return {"value": 1}

    async def fake_acall_raw(**kwargs):  # noqa: ANN003, ANN202
        seen.append(("postman", kwargs["postman"]))
        return ("raw",)

    async def fake_aiterate_raw(**kwargs):  # noqa: ANN003, ANN202
        seen.append(("postman", kwargs["postman"]))
        yield ("raw",)

    async def fake_aexpand_returns(  # noqa: ANN202
        action_obj: object,
        returns: object,
        structure_registry: object,
        origin: object = None,
    ):
        assert returns == ("raw",)
        seen.append(("expand", structure_registry, origin))
        return ("expanded",)

    monkeypatch.setattr(invoke, "ashrink_args", fake_ashrink_args)
    monkeypatch.setattr(invoke, "_acall_raw", fake_acall_raw)
    monkeypatch.setattr(invoke, "_aiterate_raw", fake_aiterate_raw)
    monkeypatch.setattr(invoke, "aexpand_returns", fake_aexpand_returns)


@pytest.mark.asyncio
async def test_a_call_is_made_by_reference_through_the_callers_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No registry on either half; the results expand through the caller's run."""
    seen: list = []
    _patch(monkeypatch, seen)
    run, postman = object(), cast(Any, object())

    result = await invoke._acall(
        Action.model_construct(id="action-1"), postman=postman, origin=run
    )

    assert result == "expanded"
    assert seen == [("shrink", None), ("postman", postman), ("expand", None, run)]


@pytest.mark.asyncio
async def test_a_stream_expands_every_raw_yield(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list = []
    _patch(monkeypatch, seen)
    run, postman = object(), cast(Any, object())

    results = [
        item
        async for item in invoke._aiterate(
            Action.model_construct(id="action-1"), postman=postman, origin=run
        )
    ]

    assert results == ["expanded"]
    assert seen == [("shrink", None), ("postman", postman), ("expand", None, run)]


# --------------------------------------------------------------------------- #
# What used to be a deferred ValueError is now a signature error
# --------------------------------------------------------------------------- #


def test_a_postman_is_not_optional() -> None:
    """Formerly ``postman: Postman | None = None`` plus a resolver that only raised.

    The failure moved from a ``ValueError`` at run time to a ``TypeError`` at the call --
    which is the entire point, so assert it rather than deleting the old test.
    """
    with pytest.raises(TypeError, match="postman"):
        invoke._acall(Action.model_construct(id="a"))  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "dead", ["cached", "log", "options", "task", "structure_registry"]
)
def test_the_dead_options_are_gone(dead: str) -> None:
    """``cached`` and ``log`` were accepted and then ``del``'d on the first line of four
    bodies; ``options``/``task`` were the merge layer that made the resolvers necessary;
    an untyped call has no registry.
    """
    for fn in (
        invoke._acall,
        invoke._aiterate,
        invoke._acall_raw,
        invoke._aiterate_raw,
    ):
        assert dead not in inspect.signature(fn).parameters, f"{fn.__name__}: {dead}"


def test_the_module_it_replaced_is_gone() -> None:
    """So that nothing quietly resurrects the optional-postman surface."""
    with pytest.raises(ModuleNotFoundError):
        import rekuest.client.remote  # noqa: F401


def test_the_parent_is_only_ever_what_was_passed() -> None:
    """Unchanged, and still true: there is no ambient parent to discover."""
    from arkitekt_runtime.calls import _resolve_parent

    assert _resolve_parent(None) is None
