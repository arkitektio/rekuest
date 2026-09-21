"""Turning what the server returns into the catalogs the rules run against. No network."""

from rekuest.api.schema import (
    BaseCatalogQueryBaseCatalog,
    CatalogArgument,
    CatalogComponent,
    CatalogOperation,
    CatalogProp,
    CatalogValueKind,
    UICatalog,
)
from rekuest.catalogs import BASE_CATALOG_DRIFT, load_base_catalog
from rekuest.catalogs.remote import (
    catalog_from_base_catalog,
    catalog_from_ui_catalog,
    check_base_catalog_drift,
)


def _ui_catalog(is_registered: bool = True) -> UICatalog:
    return UICatalog(
        id="1",
        name="electron",
        description="the desktop app",
        isRegistered=is_registered,
        components=(
            CatalogComponent(
                name="Slider",
                description=None,
                acceptsChildren=False,
                props=(
                    CatalogProp(
                        key="min",
                        kind=CatalogValueKind.FLOAT,
                        required=True,
                        description=None,
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


def test_a_ui_catalog_round_trips_into_a_catalog() -> None:
    catalog = catalog_from_ui_catalog(_ui_catalog())

    assert catalog.name == "electron" and catalog.registered is True
    assert catalog.components[0].name == "Slider"
    assert catalog.components[0].accepts_children is False
    assert catalog.components[0].props[0].kind == "FLOAT"
    assert catalog.components[0].props[0].required is True
    assert catalog.operations[0].keys == ("value",)
    assert catalog.operations[0].returns == "FLOAT"


def test_kinds_arrive_as_plain_strings() -> None:
    """The rules compare kinds to plain strings, so an enum must not leak through."""
    catalog = catalog_from_ui_catalog(_ui_catalog())
    assert isinstance(catalog.components[0].props[0].kind, str)
    assert type(catalog.components[0].props[0].kind) is str


def test_an_unregistered_catalog_says_so() -> None:
    """Its operations are not authoritative, so unknown ones must not warn."""
    assert catalog_from_ui_catalog(_ui_catalog(is_registered=False)).registered is False


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
