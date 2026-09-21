"""Calling something, described by ids rather than by fetched models.

This is the half of the call surface that needs no GraphQL: a target is an id,
the postman is handed in, and nothing here knows what an ``Action`` or an
``Implementation`` is. The agent runtime calls through here -- dependency method
calls in particular, which never have a fetched target to begin with.

The other half, which resolves a fetched ``Action``/``Implementation`` into
those ids, is :mod:`rekuest.client.remote`. Keeping them apart is what lets
``agents.hooks.startup`` and ``actors.dependency`` import a call helper without
dragging the generated client surface into the agnostic runtime.
"""

import uuid
from dataclasses import dataclass, replace as dc_replace
from typing import Any
from collections.abc import AsyncGenerator

from koil import unkoil
from rath.scalars import ID

from rekuest.api.schema import DefinitionInput, HookInput, TaskEventKind
from rekuest.errors import CriticalCallError, ErrorCallError
from rekuest.messages import Assign, JSONSerializable
from rekuest.postmans.types import Postman
from rekuest.structures.registry import StructureRegistry
from rekuest.structures.serialization.actor import (
    aexpand_actor_returns,
    ashrink_actor_args,
)

__all__: list[str] = []


def ensure_return_as_tuple(value: Any) -> tuple[Any]:  # noqa: ANN401
    """Ensure that the value is a list."""
    if not value:
        return tuple()
    if isinstance(value, tuple):
        return value  # type: ignore
    return tuple([value])


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
    action_id: ID | None = None,
    implementation_id: ID | None = None,
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
        action=action_id,
        implementation=implementation_id,
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


def call_dependency(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Synchronously call a method on a dependency.

    Blocking counterpart to :func:`acall_dependency` (see there for
    parameters).
    """
    return unkoil(acall_dependency, *args, **kwargs)


def call_dependency_raw(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    """Synchronously call a method on a dependency with already serialized arguments."""
    return unkoil(acall_dependency_raw, *args, **kwargs)
