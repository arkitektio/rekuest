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


def test_rekuest_defines_no_context_variable_of_its_own() -> None:
    """There is exactly one ambient thing, and rekuest does not own it.

    The task an action runs for is ambient -- `rath.task.current_task`, which the
    actor sets around the body so the clients it was handed can attribute their
    requests. rekuest *reads* that one. It defines none: a second ambient thing is
    how two apps in one process start sharing state again, and the import check
    this replaced could not see the difference (it only looked for
    `import contextvars`, which reading rath's does not need).
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        defined = [
            node.lineno
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "ContextVar")
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "ContextVar"
                )
            )
        ]
        if defined:
            offenders[str(path.relative_to(PACKAGE))] = defined

    assert not offenders, (
        f"rekuest defines context variables: {offenders}. The one ambient value is "
        "rath's current task; anything else a call needs, it is handed."
    )


def test_no_module_stamps_app_state_on_user_objects() -> None:
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text()
        hits = [stamp for stamp in REMOVED_STAMPS if stamp in source]
        if hits:
            offenders.append(f"{path.relative_to(PACKAGE)}: {hits}")
    assert not offenders, f"an app's rules belong on its registry, not the class: {offenders}"


# --------------------------------------------------------------------------- #
# The generated operations must not be able to collide with the client's own names
# --------------------------------------------------------------------------- #

#: The class the generated mixin is mixed into, whose names must be reserved.
CLIENT_CLASS = "rekuest.client.client.Rekuest"

#: turms' dumped configuration, beside the module it generated.
DUMPED_CONFIG = Path(__file__).resolve().parents[1] / "rekuest/api/project.json"


def test_the_client_reserves_its_own_names_by_deriving_them() -> None:
    """``reserved_from`` names the client itself, and nothing is hand-listed.

    Every operation turms generates becomes a method of a mixin the client mixes
    in, so those methods and the client's pydantic *fields* share one namespace --
    and pydantic lets a field hide a same-named method. turms turns that into a
    build error, but only for the names it is told to reserve.

    Pointing ``reserved_from`` at the client makes turms derive the whole set
    (``names_reserved_by`` walks the MRO and the model fields, skipping the mixin
    it is about to regenerate). A hand-written ``reserved_names`` drifts instead:
    mikro's kept two names for months after they were deleted, while never
    covering ``federated_expansion`` -- a real field it was supposed to protect.

    Checked against the dumped configuration rather than the yaml, because that is
    the configuration the checked-in module was actually generated with, and it
    needs no yaml parser in the test environment.
    """
    import json

    config = json.loads(DUMPED_CONFIG.read_text())
    for plugin in config["extensions"]["turms"].get("plugins", []):
        if not plugin["type"].endswith("client.ClientPlugin"):
            continue
        assert "reserved_names" not in plugin, (
            "reserved_names is hand-maintained and drifts; let reserved_from "
            "derive the set from the client class"
        )
        assert plugin.get("reserved_from") == [CLIENT_CLASS], (
            f"reserved_from must be [{CLIENT_CLASS!r}] -- the client the "
            "generated mixin lands in -- so its fields are reserved"
        )
