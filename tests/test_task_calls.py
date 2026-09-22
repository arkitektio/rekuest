"""A call made inside a task goes through the task.

The task is the parent, so it supplies the three things a child call needs -- the agent's
socket, the parent assignment and the actor's registry -- and none of them is looked up
from context. See :mod:`rekuest.invoke` for why the target is typed structurally.
"""

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from koil.composition.base import KoiledModel
from rath.origin import ContextBound

from rekuest.invoke import CallTarget, ImplementationTarget
from rekuest.task import Task
from rekuest.traits.action import Callable

PACKAGE = Path(__file__).resolve().parent.parent / "rekuest"


# --------------------------------------------------------------------------- #
# A task no agent runs cannot be a parent
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", ["call", "iterate"])
def test_a_local_task_refuses_to_be_a_parent(method: str) -> None:
    """``Task.local()`` has no agent and no assignment, so a child call has no socket
    to leave over and nothing to hang off. It must say so, and say what to do instead.

    Checked on the *sync* wrappers on purpose: they run ``unkoil``, and if the refusal
    ever moved after that the failure would be koil complaining about a missing loop
    rather than this message.
    """
    with pytest.raises(ValueError, match="runs for no assignment"):
        getattr(Task.local(), method)(object())


def test_the_local_refusal_points_at_the_client() -> None:
    """A parentless call is exactly what the client is for; the message should say so."""
    with pytest.raises(ValueError, match=r"rekuest\.call\(action"):
        Task.local().call(object())


# --------------------------------------------------------------------------- #
# What a target is
# --------------------------------------------------------------------------- #


class _Action:
    """Structurally an action: ports to serialize by and an id to name it."""

    id = "action-1"
    args = ()
    returns = ()


class _Implementation:
    """Structurally an implementation: its own id, wrapping an action."""

    id = "impl-1"
    action = _Action()


def test_an_action_is_a_call_target() -> None:
    assert isinstance(_Action(), CallTarget)
    assert not isinstance(_Action(), ImplementationTarget)


def test_an_implementation_is_told_apart_by_carrying_an_action() -> None:
    """The discriminator is ``action``, which an action does not have -- so the two are
    distinguished without either generated class being named outside the client layer.
    """
    assert isinstance(_Implementation(), ImplementationTarget)


def test_a_target_that_is_neither_is_refused_by_name() -> None:
    from rekuest.invoke import _resolve_target

    with pytest.raises(ValueError, match="an Action or an Implementation"):
        _resolve_target(object())  # type: ignore[arg-type]


def test_resolving_an_implementation_yields_its_action_and_its_own_id() -> None:
    from rekuest.invoke import _resolve_target

    action, implementation_id = _resolve_target(_Implementation())  # type: ignore[arg-type]
    assert action.id == "action-1"
    assert implementation_id == "impl-1"


# --------------------------------------------------------------------------- #
# The MRO trap
# --------------------------------------------------------------------------- #


def test_context_bound_comes_before_koiled_model() -> None:
    """Base order decides whether a fetched action knows its client, and gets it wrong
    *silently*.

    ``KoiledModel`` defines its own ``model_post_init`` (pydantic's
    ``init_private_attributes``) and does not chain to ``super()``. With ``KoiledModel``
    ahead of ``ContextBound`` in the MRO, ``ContextBound.model_post_init`` never runs,
    the validation context is dropped and ``bound_client()`` returns ``None`` -- with no
    error anywhere. A comment cannot catch that; this can.
    """
    mro = Callable.__mro__
    assert mro.index(ContextBound) < mro.index(KoiledModel), (
        "ContextBound must precede KoiledModel in Callable's bases, or bound_client() "
        "silently returns None"
    )


def test_a_fetched_action_remembers_its_client() -> None:
    """The point of the base order, end to end."""
    from rath.origin import origin_context

    class _Fetched(Callable):
        id: str

    client = object()
    fetched = _Fetched.model_validate(
        {"id": "1"}, context=origin_context(client=client)
    )
    assert fetched.bound_client() is client


def test_an_action_built_outside_a_client_knows_none() -> None:
    """``model_construct`` and a bare ``model_validate`` carry no origin. Callers have to
    handle that, so pin that it really is ``None`` rather than something truthy.
    """

    class _Fetched(Callable):
        id: str

    assert _Fetched.model_validate({"id": "1"}).bound_client() is None


# --------------------------------------------------------------------------- #
# The layering that lets a task call at all
# --------------------------------------------------------------------------- #


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
    return names


@pytest.mark.parametrize("module", ["invoke.py", "task.py"])
def test_the_calling_path_does_not_name_the_generated_surface(module: str) -> None:
    """``tests/test_layering.py`` enforces this for the package as a whole; these two are
    called out by name because they are the ones that wanted to.

    A task must be able to call, and a call needs ports and an id -- so the target is
    described structurally instead, which is the same move
    ``structures/serialization/protocols.py`` already makes.
    """
    reached = [n for n in _imports(PACKAGE / module) if "rekuest.api" in n]
    assert not reached, f"{module} names {reached}"


def test_importing_the_calls_layer_stays_free_of_the_generated_surface() -> None:
    """Why :mod:`rekuest.invoke` exists as its own module rather than living in
    ``calls.py``: the agnostic runtime imports ``rekuest.calls`` at module scope, and
    folding the fragment-holding half into it would drag the whole generated client
    surface into every agent.

    A subprocess, because the test session has already imported the client.
    """
    code = (
        "import sys, rekuest.calls;"
        "print('rekuest.api.schema' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False", out.stdout


def test_a_task_carries_no_call_state_of_its_own() -> None:
    """Everything a child call needs is read off the assignment when the call is made."""
    assert not hasattr(Task, "postman")
    assert not hasattr(Task, "structure_registry")
    for method in ("acall", "call", "aiterate", "iterate", "acall_raw", "aiterate_raw"):
        assert callable(getattr(Task, method)), method


def test_no_task_method_takes_a_parent() -> None:
    """The task *is* the parent; accepting one would let a caller lie about it."""
    import inspect

    for method in ("acall", "aiterate", "acall_raw", "aiterate_raw"):
        params = inspect.signature(getattr(Task, method)).parameters
        assert "parent" not in params, method
        assert "postman" not in params, method


def test_task_call_options_do_not_include_the_dead_ones() -> None:
    """``cached`` and ``log`` were accepted and then ``del``'d for four signatures."""
    import inspect

    params: dict[str, Any] = dict(inspect.signature(Task.acall).parameters)
    assert "cached" not in params
    assert "log" not in params
