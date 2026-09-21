"""Validate an agent's payload against catalogs a server describes.

:mod:`rekuest.catalogs` holds the rules and the data; this module converts the server's
GraphQL catalog objects into that data and runs the rules over an ``ImplementAgentInput``,
so it lives beside that module rather than inside it (importing anything from ``rekuest``
there is a cycle -- see that module's warning).

The agent does not use it: it registers over its socket and the server validates there,
refusing a declaration that does not fit and reporting the soft findings on ``Init``. This
is for a user-side tool that holds a client and wants the findings before it connects.
"""

from collections.abc import Sequence

from rekuest.protocol.schema import ImplementAgentInput, OptimisticInput
from rekuest.api.schema import BaseCatalogQueryBaseCatalog, CatalogOperation, UICatalog
from rekuest.catalogs import (
    ArgumentSpec,
    BASE_CATALOG_DRIFT,
    Catalog,
    ComponentSpec,
    Diagnostic,
    OperationSpec,
    PropSpec,
    WARNING,
    base_operations,
    check_catalog_names,
    load_base_catalog,
    resolve_catalogs,
)

DEFAULT_CATALOG_NAME = "default"
"""What the server calls the catalog it creates for a blok that names none."""


def _operation_spec(operation: CatalogOperation) -> OperationSpec:
    return OperationSpec(
        name=operation.name,
        returns=str(operation.returns.value),
        arguments=tuple(
            ArgumentSpec(
                key=argument.key,
                kind=str(argument.kind.value),
                required=argument.required,
                description=argument.description,
            )
            for argument in operation.arguments
        ),
        description=operation.description,
    )


def catalog_from_ui_catalog(ui: UICatalog) -> Catalog:
    """A fetched ``UICatalog`` as a :class:`~rekuest.catalogs.Catalog`."""
    return Catalog(
        name=ui.name,
        operations=tuple(_operation_spec(operation) for operation in ui.operations),
        components=tuple(
            ComponentSpec(
                name=component.name,
                accepts_children=component.accepts_children,
                props=tuple(
                    PropSpec(
                        key=prop.key,
                        kind=str(prop.kind.value),
                        required=prop.required,
                        description=prop.description,
                    )
                    for prop in component.props
                ),
                description=component.description,
            )
            for component in ui.components
        ),
        description=ui.description,
        registered=ui.is_registered,
    )


def catalog_from_base_catalog(base: BaseCatalogQueryBaseCatalog) -> Catalog:
    """The server's base catalog as a :class:`~rekuest.catalogs.Catalog`."""
    return Catalog(
        name=base.name,
        version=base.version,
        operations=tuple(_operation_spec(operation) for operation in base.operations),
        description=base.description,
    )


def check_base_catalog_drift(remote: Catalog) -> list[Diagnostic]:
    """Warn when the vendored base manifest disagrees with the server's.

    Client-only: the server has nothing to compare itself against, so this mirrors no
    server rule. It is the cheapest guard against a stale vendored ``base_v1.json``,
    which would otherwise make every other check quietly wrong.
    """
    vendored = load_base_catalog()
    findings: list[Diagnostic] = []

    if remote.version != vendored.version:
        findings.append(
            Diagnostic(
                level=WARNING,
                code=BASE_CATALOG_DRIFT,
                message=(
                    f"the server provides base catalog version {remote.version}, this client "
                    f"ships {vendored.version}; upgrade rekuest to validate against the same rules"
                ),
            )
        )
        return findings

    ours = base_operations()
    theirs = {operation.name: operation for operation in remote.operations}
    missing = sorted(set(theirs) - set(ours))
    extra = sorted(set(ours) - set(theirs))
    differing = sorted(name for name in set(ours) & set(theirs) if ours[name] != theirs[name])

    if missing or extra or differing:
        findings.append(
            Diagnostic(
                level=WARNING,
                code=BASE_CATALOG_DRIFT,
                message=(
                    "the vendored base catalog disagrees with the server's "
                    f"(only on the server: {missing}; only in this client: {extra}; "
                    f"defined differently: {differing})"
                ),
            )
        )
    return findings


def validate_agent_input(
    agent_input: ImplementAgentInput,
    catalogs: dict[str, Catalog],
    optimistics: Sequence[OptimisticInput] | None = None,
) -> list[Diagnostic]:
    """Check every blok and implementation of ``agent_input`` against ``catalogs``.

    Kept pure (no fetching) so the rules can be tested without a
    server. Bloks are checked against the one catalog they name, implementations against
    the catalogs their definition opted into.

    Returns:
        The non-fatal findings, in blok-then-implementation order.

    Raises:
        ValueError: If any hard catalog rule is broken. Nothing has been uploaded yet.
    """
    from rekuest.blok.validate import validate_blok_catalog
    from rekuest.definition.catalogs import validate_definition_against_catalog

    diagnostics: list[Diagnostic] = []

    for blok in agent_input.bloks or ():
        name = blok.catalog or DEFAULT_CATALOG_NAME
        named = catalogs.get(name)
        # A blok never yields unknown_catalog: the server creates the catalog it names.
        view = resolve_catalogs(
            [named] if named is not None else [],
            authoritative=named is not None and named.registered,
        )
        for component in blok.components or ():
            diagnostics.extend(validate_blok_catalog(component, view))

    for implementation in agent_input.implementations or ():
        definition = implementation.definition
        owner = f"Definition {definition.key}"
        diagnostics.extend(check_catalog_names(definition.catalogs or (), catalogs, owner))
        named = [
            catalogs[name] for name in definition.catalogs or () if name in catalogs
        ]
        view = resolve_catalogs(named, authoritative=True)
        diagnostics.extend(
            validate_definition_against_catalog(
                definition, view, implementation.optimistics or optimistics
            )
        )

    return diagnostics
