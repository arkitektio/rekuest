"""Calling a fetched ``Action`` or ``Implementation``.

The client half of the call surface: it takes the models a GraphQL query gave
back, turns them into the ids the wire wants, and hands the rest to
:mod:`rekuest.calls`, which knows nothing of either type.

The public surface is ``acall``/``call`` (single result), ``aiterate``/
``iterate`` (streaming) and their ``*_raw`` counterparts operating on
already-serialized payloads. Dependency method calls name no target and live in
:mod:`rekuest.calls`.
"""

from typing import Any
from collections.abc import AsyncGenerator, Generator

from koil import unkoil, unkoil_gen

from rekuest.api.schema import Action, HookInput, Implementation
from rekuest.calls import (
    CallOptions,
    _astream_raw,
    _resolve_options,
    _resolve_parent,
    _resolve_postman,
    _resolve_structure_registry,
)
from rekuest.messages import Assign
from rekuest.postmans.types import Postman
from rekuest.structures.registry import StructureRegistry
from rekuest.structures.serialization.postman import aexpand_returns, ashrink_args

__all__: list[str] = []


def _resolve_target(
    target: Action | Implementation,
) -> tuple[Action, Implementation | None]:
    """Resolve an action-like target into (action, implementation)."""
    if isinstance(target, Implementation):
        return target.action, target
    if isinstance(target, Action):
        return target, None
    raise ValueError("action_implementation_res must be a Action or Implementation")


async def aiterate_raw(
    kwargs: dict[str, Any] | None = None,
    action: Action | None = None,
    implementation: Implementation | None = None,
    parent: Assign | None = None,
    reference: str | None = None,
    hooks: list[HookInput] | None = None,
    cached: bool = False,
    capture: bool = False,
    log: bool = False,
    postman: Postman | None = None,
    escalate_to_interrupt: bool = False,
    cancel_timeout: float | None = None,
    options: CallOptions | None = None,
) -> AsyncGenerator[Any, None]:
    """Stream the raw YIELD payloads of a remote call.

    Operates on already-serialized arguments and yields transport-level
    payloads; prefer :func:`aiterate` unless you are deliberately operating on
    transport-level data.

    ``cached`` and ``log`` are accepted but not sent: the backend dropped both fields
    from ``AssignInput`` (``cached`` had already been documented there as having no
    effect — replay is decided caller-side via ``reusableTaskFor``). They stay in the
    signature so existing callers keep working. Prefer passing ``options``; the
    individual keyword arguments are merged onto it for backwards compatibility.
    """
    del cached, log  # accepted for compatibility, never sent
    opts = _resolve_options(
        options,
        parent=parent,
        reference=reference,
        hooks=hooks,
        capture=capture,
        postman=postman,
        escalate_to_interrupt=escalate_to_interrupt,
        cancel_timeout=cancel_timeout,
    )
    resolved_postman = _resolve_postman(opts.postman)

    async for returns in _astream_raw(
        resolved_postman,
        args=kwargs,
        reference=opts.reference,
        hooks=opts.hooks,
        capture=opts.capture,
        action_id=action.id if action else None,
        implementation_id=implementation.id if implementation else None,
        parent=_resolve_parent(opts.parent),
        escalate_to_interrupt=opts.escalate_to_interrupt,
        cancel_timeout=opts.cancel_timeout,
    ):
        yield returns


async def acall_raw(
    kwargs: dict[str, Any] | None = None,
    action: Action | None = None,
    implementation: Implementation | None = None,
    parent: Assign | None = None,
    reference: str | None = None,
    hooks: list[HookInput] | None = None,
    cached: bool = False,
    capture: bool = False,
    log: bool = False,
    postman: Postman | None = None,
    escalate_to_interrupt: bool = False,
    cancel_timeout: float | None = None,
    options: CallOptions | None = None,
) -> Any:  # noqa: ANN401
    """Execute a low-level remote call with already serialized arguments.

    Sends a task through the current postman and returns the raw
    backend payload of the final ``YIELD`` event. It does not shrink Python
    arguments or expand returned structures; prefer :func:`acall` unless you
    are deliberately operating on transport-level payloads.

    Raises:
        ValueError: If no postman is available.
        ErrorCallError: If the backend reports a recoverable task error.
        CriticalCallError: If the backend reports a critical task error.
    """
    del cached, log  # accepted for compatibility, never sent
    returns = tuple()

    async for r in aiterate_raw(
        kwargs=kwargs,
        action=action,
        implementation=implementation,
        options=_resolve_options(
            options,
            parent=parent,
            reference=reference,
            hooks=hooks,
            capture=capture,
            postman=postman,
            escalate_to_interrupt=escalate_to_interrupt,
            cancel_timeout=cancel_timeout,
        ),
    ):
        returns = r

    return returns


async def acall(
    action_implementation_res: Action | Implementation,
    *args: Any,  # noqa: ANN401
    reference: str | None = None,
    hooks: list[HookInput] | None = None,
    cached: bool = False,
    parent: Assign | None = None,
    log: bool = False,
    capture: bool = False,
    structure_registry: StructureRegistry | None = None,
    postman: Postman | None = None,
    escalate_to_interrupt: bool = False,
    cancel_timeout: float | None = None,
    options: CallOptions | None = None,
    **kwargs: Any,  # noqa: ANN401
) -> Any:
    """Execute a remote action and return expanded Python values.

    The helper accepts an :class:`Action` or :class:`Implementation`. It
    resolves the target action, shrinks Python
    arguments with the structure registry, performs the remote call via
    :func:`acall_raw`, and expands the returned transport payload back into
    Python objects.

    Single-value returns are unwrapped for convenience. Multiple returns are
    returned as a tuple.

    Args:
        action_implementation_res: Action-like target to execute.
        *args: Positional Python arguments matching the action definition.
        reference: Optional client-side reference for the task.
        hooks: Hook inputs to attach to the task.
        cached: Whether cached results may be reused.
        parent: Optional parent task. When omitted, the current
            task is used if available.
        log: Whether the remote execution should persist logs.
        capture: Whether outputs should be captured remotely.
        structure_registry: Structure registry used for shrinking and expanding
            structured values.
        postman: Postman override. Defaults to the current postman context.

    Returns:
        The expanded return value, or a tuple of values for multi-return
        actions.

    Raises:
        ValueError: If the target object is not an action or implementation.
        ErrorCallError: If the backend reports a task error.
        CriticalCallError: If the backend reports a critical task error.

    Examples:
        Call an action asynchronously and receive expanded Python objects::

            result = await acall(action, image=my_image, threshold=0.5)
    """
    action, implementation = _resolve_target(action_implementation_res)
    structure_registry = _resolve_structure_registry(structure_registry)

    shrinked_args = await ashrink_args(
        action, args, kwargs, structure_registry=structure_registry
    )

    del cached, log  # accepted for compatibility, never sent
    raw_returns = await acall_raw(
        kwargs=shrinked_args,
        action=action,
        implementation=implementation,
        options=_resolve_options(
            options,
            parent=parent,
            reference=reference,
            hooks=hooks,
            capture=capture,
            postman=postman,
            escalate_to_interrupt=escalate_to_interrupt,
            cancel_timeout=cancel_timeout,
        ),
    )

    returns = await aexpand_returns(
        action,
        raw_returns,
        structure_registry=structure_registry,
    )
    if len(returns) == 1:
        return returns[0]
    return returns


async def aiterate(
    action_implementation_res: Action | Implementation,
    *args: Any,  # noqa: ANN401
    reference: str | None = None,
    hooks: list[HookInput] | None = None,
    cached: bool = False,
    parent: Assign | None = None,
    log: bool = False,
    capture: bool = False,
    structure_registry: StructureRegistry | None = None,
    postman: Postman | None = None,
    escalate_to_interrupt: bool = False,
    cancel_timeout: float | None = None,
    options: CallOptions | None = None,
    **kwargs: Any,  # noqa: ANN401
) -> AsyncGenerator[Any, None]:
    """Stream expanded yield values from a remote action.

    This helper follows the same target-resolution and structure-conversion flow
    as :func:`acall`, but yields each intermediate ``YIELD`` payload from the
    backend as soon as it arrives. Each yield is expanded through the structure
    registry before being exposed to the caller.

    Single-value yields are unwrapped for convenience. Multi-value yields are
    emitted as tuples.

    Args:
        action_implementation_res: Action-like target to execute.
        *args: Positional Python arguments matching the action definition.
        reference: Optional client-side reference for the task.
        hooks: Hook inputs to attach to the task.
        cached: Whether cached results may be reused.
        parent: Optional parent task. When omitted, the current
            task is used if available.
        log: Whether the remote execution should persist logs.
        capture: Whether outputs should be captured remotely.
        structure_registry: Structure registry used for shrinking and expanding
            structured values.
        postman: Postman override. Defaults to the current postman context.

    Yields:
        Expanded yielded values from the remote task.

    Raises:
        ValueError: If the target object is not an action or implementation.
        ErrorCallError: If the backend reports a task error.
        CriticalCallError: If the backend reports a critical task error.

    Examples:
        Stream intermediate results from a remote generator-like action::

            async for chunk in aiterate(action, prompt="hello"):
                print(chunk)
    """
    action, implementation = _resolve_target(action_implementation_res)
    structure_registry = _resolve_structure_registry(structure_registry)

    shrinked_args = await ashrink_args(
        action, args, kwargs, structure_registry=structure_registry
    )

    del cached, log  # accepted for compatibility, never sent
    async for raw_returns in aiterate_raw(
        kwargs=shrinked_args,
        action=action,
        implementation=implementation,
        options=_resolve_options(
            options,
            parent=parent,
            reference=reference,
            hooks=hooks,
            capture=capture,
            postman=postman,
            escalate_to_interrupt=escalate_to_interrupt,
            cancel_timeout=cancel_timeout,
        ),
    ):
        returns = await aexpand_returns(
            action,
            raw_returns,
            structure_registry=structure_registry,
        )
        if len(returns) == 1:
            yield returns[0]
        else:
            yield returns


def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Synchronously execute a remote action and return expanded values.

    Blocking counterpart to :func:`acall` (see there for parameters); bridges
    into the async implementation via ``unkoil``.
    """
    return unkoil(acall, *args, **kwargs)


def iterate(*args: Any, **kwargs: Any) -> Generator[Any, None, None]:  # noqa: ANN401
    """Synchronously stream expanded yield values from a remote action.

    Blocking counterpart to :func:`aiterate` (see there for parameters);
    adapts the async iterator through ``unkoil_gen``.
    """
    return unkoil_gen(aiterate, *args, **kwargs)


def call_raw(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Synchronously execute a low-level remote call with already serialized arguments."""
    return unkoil(acall_raw, *args, **kwargs)
