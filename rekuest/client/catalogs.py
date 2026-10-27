"""Validate an agent's payload against catalogs a server describes.

:mod:`rekuest.catalogs` holds the rules and the data; this module converts the server's
GraphQL catalog objects into that data and runs the rules over an ``ImplementAgentInput``,
so it lives beside that module rather than inside it (importing anything from ``rekuest``
there is a cycle -- see that module's warning).

The agent does not use it: it registers over its socket and the server validates there,
refusing a declaration that does not fit and reporting the soft findings on ``Init``. This
is for a user-side tool that holds a client and wants the findings before it connects.
"""

from collections.abc import Mapping, Sequence

from arkitekt_spec.actions import (
    ActionBlokInput,
    AgentDependencyInput,
    BlokImplementationInput,
    ComponentContext,
    ImplementAgentInput,
    OptimisticInput,
)
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.catalogs import ORKESTRATOR_MODULE, BlokView, blok_view, default_view, orkestrator_module
from arkitekt_spec.blok.vocabulary import BlokError, Finding
from arkitekt_spec.declare.blok.infer import ArgPorts, resolve_scope
from arkitekt_spec.declare.blok.validate import BlokScope, check_blok_tree
from rekuest.api.schema import BaseCatalogQueryBaseCatalog, CatalogOperation, Module
from arkitekt_spec.declare.catalogs import (
    ArgumentSpec,
    BASE_CATALOG_DRIFT,
    Catalog,
    ComponentSpec,
    Diagnostic,
    EventFieldSpec,
    OperationSpec,
    PropSpec,
    WARNING,
    base_operations,
    check_catalog_names,
    is_standard_catalog,
    load_base_catalog,
    resolve_catalogs,
)

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


def catalog_from_module(module: Module) -> Catalog:
    """A fetched ``Module`` (an app's own unversioned extras) as a
    :class:`~arkitekt_spec.declare.catalogs.Catalog`: what a blok naming it as its ``module``
    is drawn with."""
    return Catalog(
        name=module.name,
        operations=tuple(_operation_spec(operation) for operation in module.operations),
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
                        event=tuple(
                            EventFieldSpec(key=field.key, kind=str(field.kind.value), description=field.description)
                            for field in prop.event
                        )
                        if prop.event is not None
                        else None,
                    )
                    for prop in component.props
                ),
                description=component.description,
                context=str(component.context.value),
                children_context=str(component.children_context.value) if component.children_context is not None else None,
                inherits_context=component.inherits_context,
                downstream_of=component.downstream_of,
                not_downstream_of=component.not_downstream_of,
                text_prop=component.text_prop,
                catalog=component.catalog,
            )
            for component in module.components
        ),
        description=module.description,
        registered=module.is_registered,
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


def _registered(module: Catalog) -> bool:
    """Whether the server holds ``module`` as registered: it registered components or operations."""
    return module.registered and bool(module.components or module.operations)


def blok_view_for(name: str | None, modules: Mapping[str, Catalog]) -> BlokView:
    """The catalogs and the module a blok naming the module ``name`` is drawn with, as the
    server decides it.

    Base, the standard catalogs and the Orkestrator's module always. A module the server holds
    is added after them: a component it
    defines that a standard catalog also defines is drawn as the module says (``catalog.shadow``
    where a tree uses it), and one that registered no components leaves every component
    unknowable. A name the server holds nothing for is checked against the Orkestrator's
    module, none reported unknown (:func:`module_findings` warns about it).
    """
    if is_standard_catalog(name):
        return default_view()
    named = modules.get(name) if name is not None else None
    if named is None or not _registered(named):
        return blok_view([orkestrator_module()], complete=False)
    return blok_view([orkestrator_module(), named], complete=bool(named.components))


def module_findings(name: str | None, modules: Mapping[str, Catalog]) -> list[Finding]:
    """``module.unknown`` when ``name`` is neither the Orkestrator's nor a module the server
    holds as registered: nothing then checks the components the blok takes from it."""
    if name is None or is_standard_catalog(name):
        return []
    named = modules.get(name)
    if named is not None and _registered(named):
        return []
    registered = ", ".join(sorted({ORKESTRATOR_MODULE, *(key for key, catalog in modules.items() if _registered(catalog))}))
    return [Finding.of("module.unknown", f"module {name}", module=name, modules=registered)]


def _scope(
    kind: str,
    context: ComponentContext,
    blok: BlokImplementationInput | ActionBlokInput,
    dependencies: Sequence[AgentDependencyInput],
    args: Sequence[str] | None,
) -> BlokScope:
    return BlokScope(
        kind=kind,
        context=context.value,
        dependencies=tuple(dependencies),
        locals=blok.locals,
        queries=blok.queries,
        subscriptions=blok.subscriptions,
        args=tuple(args) if args is not None else None,
        catalogs=blok.catalogs,
        demo=blok.demo,
    )


def _check_blok(
    owner: str,
    blok: BlokImplementationInput | ActionBlokInput,
    scope: BlokScope,
    catalogs: Mapping[str, Catalog],
    arg_ports: ArgPorts | None = None,
) -> list[Diagnostic]:
    """The warnings of one blok, every tree it sends, against the module it names.

    The blok's locals travel as written; each tree is checked against them as the server
    resolves them, with ``arg_ports`` (an agent blok's own actions, by ``self``).

    Raises:
        BlokError: On the first error, with its code (a ``ValueError``).
    """
    view = blok_view_for(blok.module, catalogs)
    trees = list(blok.components or ())
    if blok.source is not None:
        try:
            trees.append(parse_bsx(blok.source, view=view))
        except BlokError as error:
            raise error.within(owner) from None
    diagnostics = [
        Diagnostic(level=WARNING, code=finding.code, message=f"{owner}: {finding.message}", path=finding.path)
        for finding in module_findings(blok.module, catalogs)
    ]
    for tree in trees:
        resolved = resolve_scope(tree, scope, view, arg_ports=arg_ports)
        for finding in check_blok_tree(tree, resolved, view):
            located = Finding(
                code=finding.code,
                level=finding.level,
                message=f"{owner}: {finding.message}",
                path=finding.path,
            )
            if finding.level == "error":
                raise BlokError.from_finding(located)
            diagnostics.append(
                Diagnostic(level=WARNING, code=finding.code, message=located.message, path=finding.path)
            )
    return diagnostics


def validate_agent_input(
    agent_input: ImplementAgentInput,
    catalogs: dict[str, Catalog],
    optimistics: Sequence[OptimisticInput] | None = None,
) -> list[Diagnostic]:
    """Check every blok and implementation of ``agent_input`` against ``catalogs``.

    Kept pure (no fetching) so the rules can be tested without a server. A blok (the
    agent's own, and an action's own) is checked by the spec's blok validator against the
    module it names (:func:`blok_view_for`); an implementation against the catalogs its
    definition opted into.

    Returns:
        The non-fatal findings, in blok-then-implementation order. A blok's carry the code of
        the spec's diagnostic table (``catalog.unknown_component``).

    Raises:
        ValueError: If a rule that is never only a warning is broken (a blok's is a
            :class:`~arkitekt_spec.blok.vocabulary.BlokError` with its code). Nothing has
            been uploaded yet.
    """
    from arkitekt_spec.declare.definition.catalogs import validate_definition_against_catalog

    diagnostics: list[Diagnostic] = []
    # An agent blok's ``self`` is the agent: its locals resolve against its own actions' full
    # arg ports, as the server resolves them (a local passed to an ENUM takes its choices).
    own_arg_ports: ArgPorts = {
        "self": {
            implementation.interface: implementation.definition.args
            for implementation in agent_input.implementations or ()
            if implementation.interface is not None
        }
    }

    for blok in agent_input.bloks or ():
        context = ComponentContext(blok.context)
        kind = "action_logo" if context == ComponentContext.ACTION_LOGO else "agent"
        scope = _scope(kind, context, blok, blok.dependencies, [port.key for port in blok.args or ()])
        arg_ports = own_arg_ports if kind == "agent" else None
        diagnostics.extend(_check_blok(f"Blok {blok.key!r}", blok, scope, catalogs, arg_ports))

    for implementation in agent_input.implementations or ():
        definition = implementation.definition
        for action_blok in definition.bloks:
            context = ComponentContext(action_blok.context)
            scope = _scope(
                "action",
                context,
                action_blok,
                implementation.dependencies,
                [port.key for port in definition.args],
            )
            owner = f"Definition {definition.key} ({context.value.lower()} blok)"
            diagnostics.extend(_check_blok(owner, action_blok, scope, catalogs))

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
