"""The client's catalog rules, held against the server's.

A test per rule does not catch the failure that matters: the server *adding* one. So this
reads the server's ``facade/catalog_validation.py`` and asserts every rule site over there
maps onto an entry in :data:`rekuest.catalogs.RULES`. Coverage, not a bijection -- one
entry may legitimately cover several raise sites.

Skips when the server tree is not checked out, like the vendored-manifest diff.
"""

import ast
import os
from pathlib import Path

import pytest

from rekuest.protocol.schema import DiagnosticLevel
from rekuest.catalogs import (
    RULES,
    UNKNOWN_CATALOG,
    UNKNOWN_OPERATION,
    Diagnostic,
    WARNING,
)

DEFAULT_SERVER_TREE = Path("/home/jhnnsrs/Code/deployments/next/mounts/rekuest")
SERVER_TREE = Path(os.environ.get("REKUEST_SERVER_TREE", DEFAULT_SERVER_TREE))
SERVER_MODULE = SERVER_TREE / "facade" / "catalog_validation.py"


def _server_source() -> str:
    if not SERVER_MODULE.exists():
        pytest.skip(f"server tree not checked out at {SERVER_TREE}")
    return SERVER_MODULE.read_text(encoding="utf-8")


def _enclosing_functions_of_raises(source: str) -> set[str]:
    """Every function in the server module that raises ``ValueError``."""
    tree = ast.parse(source)
    found: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Raise)
                and isinstance(inner.exc, ast.Call)
                and isinstance(inner.exc.func, ast.Name)
                and inner.exc.func.id == "ValueError"
            ):
                found.add(node.name)
                break
    return found


def _diagnostic_codes(source: str) -> set[str]:
    """The diagnostic code constants the server assigns at module level."""
    tree = ast.parse(source)
    codes: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    if isinstance(node.value.value, str):
                        codes.add(node.value.value)
    return codes


def test_every_server_rule_site_is_covered() -> None:
    """A new rule on the server shows up here as an unmapped function."""
    raising = _enclosing_functions_of_raises(_server_source())
    covered = {rule.server for rule in RULES}

    # Helpers that only re-raise what a covered function raised are not rules.
    passthrough = {
        "validate_calls_against_catalogs",
        "validate_calls_against_catalog",
        "validate_components_against_catalogs",
        "validate_manifest_against_catalog",
        "validate_widgets_against_catalogs",
        "_check_calls",
        "resolve_components",
    }

    unmapped = sorted(raising - covered - passthrough)
    assert not unmapped, (
        f"the server raises in {unmapped}, which no rekuest.catalogs.RULES entry claims; "
        "mirror the rule (or add it to the passthrough set if it only re-raises)"
    )


def test_server_functions_named_by_rules_still_exist() -> None:
    """Catches the other direction: a server function renamed out from under a rule."""
    source = _server_source()
    tree = ast.parse(source)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = sorted({rule.server for rule in RULES} - defined)
    assert not missing, f"RULES name server functions that no longer exist: {missing}"


def test_diagnostic_codes_match_the_server() -> None:
    codes = _diagnostic_codes(_server_source())
    assert UNKNOWN_OPERATION in codes
    assert UNKNOWN_CATALOG in codes


def test_diagnostic_levels_are_members_of_the_generated_enum() -> None:
    """The rules use plain strings so the loader imports nothing; this ties them back.

    There is exactly one level: a hard finding is raised rather than recorded, so a
    second level appearing on the server means the split has changed.
    """
    levels = {level.value for level in DiagnosticLevel}
    assert levels == {WARNING}
    assert Diagnostic(level=WARNING, code="x", message="m").level in levels


def test_rule_ids_are_unique() -> None:
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))
