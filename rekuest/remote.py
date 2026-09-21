"""Remote-call helpers for rekuest.

The public surface is ``acall``/``call`` (single result), ``aiterate``/``iterate``
(streaming), their ``*_raw`` counterparts operating on already-serialized
payloads, and ``acall_dependency``/``call_dependency`` for dependency method
calls. All of them funnel through the same internal helpers: target
resolution, ``AssignInput`` construction, and the postman event stream.
"""

import uuid
from dataclasses import dataclass, replace as dc_replace
from typing import (
    Any,
)
from collections.abc import AsyncGenerator, Generator

from rekuest.api.schema import DefinitionInput

from koil import unkoil, unkoil_gen
from rath.scalars import ID
from rekuest.api.schema import (
    TaskEventKind,
    HookInput,
    Action,
    Implementation,
)
from rekuest.messages import Assign, JSONSerializable
from rekuest.postmans.types import Postman
from rekuest.structures.registry import (
    StructureRegistry,
)
from rekuest.structures.serialization.actor import (
    aexpand_actor_returns,
    ashrink_actor_args,
)
from rekuest.structures.serialization.postman import aexpand_returns, ashrink_args
from rekuest.errors import CriticalCallError, ErrorCallError


__all__: list[str] = []


def ensure_return_as_tuple(value: Any) -> tuple[Any]:  # noqa: ANN401
    """Ensure that the value is a list."""
    if not value:
        return tuple()
    if isinstance(value, tuple):
        return value  # type: ignore
    return tuple([value])


def _resolve_target(
    target: Action | Implementation,
) -> tuple[Action, Implementation | None]:
    """Resolve an action-like target into (action, implementation)."""
    if isinstance(target, Implementation):
        return target.action, target
    if isinstance(target, Action):
        return target, None
    raise ValueError("action_implementation_res must be a Action or Implementation")


def _resolve_postman(postman: Postman | None) -> Postman:
    """The postman to call through. There is no current one to fall back to."""
    if postman is None:
        raise ValueError(
            "No postman to call through. Call through a Rekuest client "
            "(rekuest.call(...)), which supplies its own."
        )
    return postman


def _resolve_structure_registry(
    structure_registry: StructureRegistry | None,
) -> StructureRegistry:
    """The registry to (de)serialize with. There is no current one to fall back to."""
    if structure_registry is None:
        raise ValueError(
            "No structure registry to (de)serialize with. Call through a Rekuest "
            "client (rekuest.call(...)), which supplies its own."
        )
    return structure_registry


def _resolve_parent(parent: Assign | None) -> ID | None:
    """The parent task id to attach this call to, as the socket wants it.

    Only what the caller passed: a per-task ``Rekuest`` view passes its task.
    """
    return ID.validate(parent.task) if parent is not None else None


@dataclass(frozen=True)
class CallOptions:
    """Transport-level options shared by every remote call helper.

    Bundles the parameters that are forwarded unchanged from :func:`acall` /
    :func:`aiterate` down to the postman, so each layer takes one object instead
    of re-listing a dozen keyword arguments.
    """

    reference: str | None = None
    hooks: list[HookInput] | None = None
    capture: bool = False
    parent: Assign | None = None
    postman: Postman | None = None
    escalate_to_interrupt: bool = False
    cancel_timeout: float | None = None


_DEFAULT_OPTIONS = CallOptions()


def _resolve_options(options: CallOptions | None, **overrides: Any) -> CallOptions:  # noqa: ANN401
    """Merge legacy keyword arguments onto an options object.

    Keyword arguments that differ from the ``CallOptions`` defaults win over the
    corresponding field of ``options``; defaults never clobber an explicit option.
    """
    resolved = options or _DEFAULT_OPTIONS
    effective = {
        name: value
        for name, value in overrides.items()
        if value != getattr(_DEFAULT_OPTIONS, name)
    }
    return dc_replace(resolved, **effective) if effective else resolved



async def _astream_raw(  # noqa: PLR0913 - the call description, mirrored from the protocol
    postman: Postman,
    *,
    args: dict[str, Any] | None = None,
    reference: str | None = None,
    hooks: list[HookInput] | None = None,
    capture: bool = False,
    action: Action | None = None,
    implementation: Implementation | None = None,
    parent: ID | None = None,
    dependency: ID | None = None,
    method: str | None = None,
    escalate_to_interrupt: bool = False,
    cancel_timeout: float | None = None,
) -> AsyncGenerator[Any, None]:
    """Stream the YIELD payloads of a task, returning on DONE.

    The call is described by its arguments rather than by a pre-built payload: the
    GraphQL postman and the agent postman no longer share one (see
    :meth:`rekuest.postmans.types.Postman.aassign`), so each builds its own.

    Raises:
        ErrorCallError: If the backend reports a task error.
        CriticalCallError: If the backend reports a critical task error.
        RootOnlyAssignError: If a ``parent``/``dependency``/``method`` call is routed
            to a postman that can only create root tasks.
    """
    async for i in postman.aassign(
        args=args or {},
        capture=capture,
        reference=reference or str(uuid.uuid4()),
        hooks=tuple(hooks or []),
        action=action.id if action else None,
        implementation=implementation.id if implementation else None,
        parent=parent,
        dependency=dependency,
        method=method,
        escalate_to_interrupt=escalate_to_interrupt,
        cancel_timeout=cancel_timeout,
    ):
        if i.kind == TaskEventKind.YIELD:
            yield i.returns

        if i.kind == TaskEventKind.COMPLETED:
            return

        if i.kind == TaskEventKind.FAILED:
            raise ErrorCallError(i.message)

        if i.kind == TaskEventKind.CRITICAL:
            raise CriticalCallError(i.message)

        # CANCELLED and INTERRUPTED are terminal too. Somebody else (a user in the UI,
        # an interrupt cascading down a tree) can end a task this call is waiting on;
        # without these arms the stream simply never ended and the caller hung forever.
        # The agent-side postman surfaces them the same way (``agents.caller._adapt``).
        #
        # DISCONNECTED is deliberately NOT terminal: the task's fate is unknown and its
        # agent may still come back and report the real outcome. The backend bounds that
        # wait itself (``disconnected_expiry`` → CRITICAL), so this cannot hang forever.
        if i.kind in (TaskEventKind.CANCELLED, TaskEventKind.INTERRUPTED):
            raise CriticalCallError(
                i.message
                or f"The task was {i.kind.value.lower()} before it completed."
            )


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
        action=action,
        implementation=implementation,
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


async def acall_dependency_raw(
    dependency_key: ID,
    method: str,
    kwargs: dict[str, JSONSerializable],
    reference: str | None = None,
    hooks: list[HookInput] | None = None,
    cached: bool = False,
    parent: Assign | None = None,
    capture: bool = False,
    log: bool = False,
    postman: Postman | None = None,
) -> Any:  # noqa: ANN401
    """Call a method on a dependency with already serialized arguments.

    A dependency method call is never a root, so it can only be originated over the
    agent socket — which is the postman bound while an actor body runs. Calling it from
    outside a task raises ``RootOnlyAssignError``.

    ``cached`` and ``log`` are accepted but not sent: the backend dropped both fields
    from ``AssignInput`` (``cached`` had already been documented there as having no
    effect — replay is decided caller-side via ``reusableTaskFor``). They stay in the
    signature so existing callers keep working.
    """
    resolved_postman = _resolve_postman(postman)

    returns = tuple()

    async for r in _astream_raw(
        resolved_postman,
        args=kwargs,
        reference=reference,
        hooks=hooks,
        capture=capture,
        parent=_resolve_parent(parent),
        dependency=dependency_key,
        method=method,
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


async def acall_dependency(
    definition: DefinitionInput,
    dependency_key: ID,
    method: str,
    *args: Any,  # noqa: ANN401
    reference: str | None = None,
    hooks: list[HookInput] | None = None,
    cached: bool = False,
    parent: Assign | None = None,
    capture: bool = False,
    log: bool = False,
    structure_registry: StructureRegistry | None = None,
    postman: Postman | None = None,
    **kwargs: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    """Call a method on a dependency and return expanded Python values."""
    structure_registry = _resolve_structure_registry(structure_registry)

    shrinked_args = await ashrink_actor_args(
        definition, args, kwargs, structure_registry=structure_registry
    )

    raw_returns = await acall_dependency_raw(
        kwargs=shrinked_args,
        dependency_key=dependency_key,
        method=method,
        reference=reference,
        hooks=hooks,
        cached=cached,
        parent=parent,
        capture=capture,
        log=log,
        postman=postman,
    )

    returns = await aexpand_actor_returns(
        definition, raw_returns, structure_registry
    )
    if len(returns) == 1:
        return returns[0]
    return returns


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


def call_dependency(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Synchronously call a method on a dependency.

    Blocking counterpart to :func:`acall_dependency` (see there for
    parameters).
    """
    return unkoil(acall_dependency, *args, **kwargs)


def call_dependency_raw(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Synchronously call a method on a dependency with already serialized arguments."""
    return unkoil(acall_dependency_raw, *args, **kwargs)


def call_raw(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Synchronously execute a low-level remote call with already serialized arguments."""
    return unkoil(acall_raw, *args, **kwargs)
