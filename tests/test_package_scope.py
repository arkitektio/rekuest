"""rekuest must stay importable, and correct, without arkitekt.

arkitekt imports rekuest at module scope, so a module-scope import in the other
direction is circular, and order-dependently so: ``import arkitekt`` first works,
``import rekuest`` first raises from a half-initialized module. Only the
integration module is exempt. It is imported last, behind ``try/except
ImportError``, which is what keeps the package usable on its own.
"""

import ast
from pathlib import Path

import rekuest

PACKAGE = Path(rekuest.__file__).parent
INTEGRATION_MODULES = {PACKAGE / "arkitekt.py"}
INTEGRATION_PACKAGES = {PACKAGE / "contrib" / "arkitekt"}


def _module_scope_imports(tree: ast.Module) -> list[str]:
    """Names imported where they run at import time: not inside a function body.

    ``if TYPE_CHECKING:`` blocks never run, so they do not count either.
    """
    found: list[str] = []

    def visit(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.append(node.module)
            elif isinstance(node, ast.If):
                if "TYPE_CHECKING" not in ast.unparse(node.test):
                    visit(node.body)
                visit(node.orelse)
            elif isinstance(node, (ast.Try, ast.With, ast.ClassDef)):
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(node, field, []))
                for handler in getattr(node, "handlers", []):
                    visit(handler.body)

    visit(tree.body)
    return found


def _is_integration(path: Path) -> bool:
    return path in INTEGRATION_MODULES or any(
        parent in INTEGRATION_PACKAGES for parent in path.parents
    )


def test_no_module_imports_arkitekt_at_module_scope() -> None:
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if _is_integration(path):
            continue
        imports = _module_scope_imports(ast.parse(path.read_text()))
        if any(name == "arkitekt" or name.startswith("arkitekt.") for name in imports):
            offenders.append(str(path.relative_to(PACKAGE)))

    assert not offenders, (
        f"{offenders} import arkitekt at module scope. arkitekt imports rekuest, so "
        "this is a circular import that fails depending on which is imported first. "
        "Take a reference (the bound app, `ctx=`) instead of importing."
    )


def test_the_guard_sees_what_it_should() -> None:
    """The check itself: function-level and TYPE_CHECKING imports are fine, the rest is not."""
    source = '''
import os
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from arkitekt import App
try:
    import arkitekt.builders
except ImportError:
    pass
def later():
    import arkitekt
'''
    assert _module_scope_imports(ast.parse(source)) == ["os", "typing", "arkitekt.builders"]


# --------------------------------------------------------------------------- #
# No process-wide state
# --------------------------------------------------------------------------- #

#: Attributes decorators used to write on the user's classes and functions. They
#: made a class carry one app's rules process-wide; what an app knows about a
#: class lives on that app's registry now.
REMOVED_STAMPS = (
    "__rekuest_state__",
    "__rekuest_state_config__",
    "__rekuest__dependency__",
    "__definition__",
    "__definition_hash__",
    "__is_state__",
    "__rekuest_context__",
    "__rekuest_context_locks__",
    "__rekuest_model__",
    "__rekuest_app_context__",
    "__rekuest_state_demand__",
)


def test_no_module_uses_context_variables() -> None:
    """Two apps in one process share nothing ambient: what a call needs, it is handed."""
    offenders = [
        str(path.relative_to(PACKAGE))
        for path in sorted(PACKAGE.rglob("*.py"))
        if any(
            name == "contextvars" or name.startswith("contextvars.")
            for name in _module_scope_imports(ast.parse(path.read_text()))
        )
    ]
    assert not offenders, f"context variables are back in: {offenders}"


def test_no_module_stamps_app_state_on_user_objects() -> None:
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text()
        hits = [stamp for stamp in REMOVED_STAMPS if stamp in source]
        if hits:
            offenders.append(f"{path.relative_to(PACKAGE)}: {hits}")
    assert not offenders, f"an app's rules belong on its registry, not the class: {offenders}"
