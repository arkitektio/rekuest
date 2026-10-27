"""Turning what the server returns into the catalogs the rules run against. No network."""

from rekuest.protocol.schema import CatalogValueKind, ComponentContext
from rekuest.api.schema import (
    CatalogEventField,
    BaseCatalogQueryBaseCatalog,
    CatalogArgument,
    CatalogComponent,
    CatalogOperation,
    CatalogProp,
    Module,
)
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.catalogs import blok_view
from arkitekt_spec.declare.catalogs import BASE_CATALOG_DRIFT, load_base_catalog
from rekuest.client.catalogs import (
    catalog_from_base_catalog,
    catalog_from_module,
    check_base_catalog_drift,
)


def _module(is_registered: bool = True) -> Module:
    return Module(
        id="1",
        name="electron",
        description="the desktop app",
        isRegistered=is_registered,
        catalogs=("ui@1",),
        components=(
            CatalogComponent(
                name="Slider",
                description=None,
                acceptsChildren=False,
                context=ComponentContext.HTML,
                inheritsContext=False,
                downstreamOf=(),
                notDownstreamOf=(),
                props=(
                    CatalogProp(
                        key="min",
                        kind=CatalogValueKind.FLOAT,
                        required=True,
                        description=None,
                    ),
                ),
            ),
            CatalogComponent(
                name="Button",
                description=None,
                acceptsChildren=False,
                context=ComponentContext.HTML,
                inheritsContext=False,
                downstreamOf=(),
                notDownstreamOf=(),
                textProp="label",
                catalog="ui@1",
                props=(
                    CatalogProp(key="label", kind=CatalogValueKind.STRING, required=False),
                    CatalogProp(
                        key="onClick",
                        kind=CatalogValueKind.CALLBACK,
                        required=False,
                        event=(CatalogEventField(key="shift", kind=CatalogValueKind.BOOL),),
                    ),
                ),
            ),
        ),
        operations=(
            CatalogOperation(
                name="clamp",
                description=None,
                returns=CatalogValueKind.FLOAT,
                arguments=(
                    CatalogArgument(
                        key="value",
                        kind=CatalogValueKind.FLOAT,
                        required=True,
                        description=None,
                    ),
                ),
            ),
        ),
    )


def test_a_module_round_trips_into_a_catalog() -> None:
    catalog = catalog_from_module(_module())

    assert catalog.name == "electron" and catalog.registered is True
    assert catalog.components[0].name == "Slider"
    assert catalog.components[0].accepts_children is False
    assert catalog.components[0].props[0].kind == "FLOAT"
    assert catalog.components[0].props[0].required is True
    assert catalog.operations[0].keys == ("value",)
    assert catalog.operations[0].returns == "FLOAT"


def test_a_component_keeps_its_text_prop_events_and_catalog() -> None:
    """Text children fill the text prop, so a view built from the server's catalog must know it."""
    slider, button = catalog_from_module(_module()).components

    assert slider.text_prop is None and slider.context == "HTML" and slider.catalog is None
    assert button.text_prop == "label"
    assert button.catalog == "ui@1"
    on_click = button.prop("onClick")
    assert on_click is not None and on_click.event is not None
    assert [(field.key, field.kind) for field in on_click.event] == [("shift", "BOOL")]
    label = button.prop("label")
    assert label is not None and label.event is None


def test_text_children_fill_the_text_prop_of_a_fetched_component() -> None:
    """A renderer's own module, fetched, is a view in which `<Button>Go</Button>` sets `label`."""
    view = blok_view([catalog_from_module(_module())], standard=False)

    tree = parse_bsx("<Button>Go</Button>", view=view)

    assert tree.component == "Button"
    assert [(prop.key, prop.static_value) for prop in tree.props or ()] == [("label", "Go")]


def test_kinds_arrive_as_plain_strings() -> None:
    """The rules compare kinds to plain strings, so an enum must not leak through."""
    catalog = catalog_from_module(_module())
    assert isinstance(catalog.components[0].props[0].kind, str)
    assert type(catalog.components[0].props[0].kind) is str


def test_an_unregistered_module_says_so() -> None:
    """Its operations are not authoritative, so unknown ones must not warn."""
    assert catalog_from_module(_module(is_registered=False)).registered is False


def _remote_base(version: int = 1, operations=None) -> BaseCatalogQueryBaseCatalog:
    vendored = load_base_catalog()
    if operations is None:
        operations = tuple(
            CatalogOperation(
                name=operation.name,
                description=operation.description,
                returns=CatalogValueKind(operation.returns),
                arguments=tuple(
                    CatalogArgument(
                        key=argument.key,
                        kind=CatalogValueKind(argument.kind),
                        required=argument.required,
                        description=argument.description,
                    )
                    for argument in operation.arguments
                ),
            )
            for operation in vendored.operations
        )
    return BaseCatalogQueryBaseCatalog(
        name="base",
        version=version,
        description=vendored.description,
        operations=operations,
    )


def test_a_matching_base_catalog_reports_no_drift() -> None:
    assert check_base_catalog_drift(catalog_from_base_catalog(_remote_base())) == []


def test_a_different_base_version_is_drift() -> None:
    findings = check_base_catalog_drift(catalog_from_base_catalog(_remote_base(version=2)))
    assert [f.code for f in findings] == [BASE_CATALOG_DRIFT]
    assert "upgrade rekuest" in findings[0].message


def test_a_missing_operation_is_drift() -> None:
    full = _remote_base()
    trimmed = _remote_base(operations=full.operations[:-1])
    findings = check_base_catalog_drift(catalog_from_base_catalog(trimmed))
    assert [f.code for f in findings] == [BASE_CATALOG_DRIFT]
    assert "only in this client" in findings[0].message


def test_drift_is_a_warning_not_an_error() -> None:
    """A stale vendored manifest must not stop an agent registering."""
    findings = check_base_catalog_drift(catalog_from_base_catalog(_remote_base(version=99)))
    assert all(f.level == "WARNING" for f in findings)
