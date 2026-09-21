"""Only the client layer may speak the generated client surface.

``rekuest/api/schema.py`` holds two unrelated things: the *protocol vocabulary*
an app speaks (inputs, enums) and the *client surface* (fragments, operations
and the ``RekuestApi`` mixin). An agent needs the first and never the second --
it registers by connecting, and nothing in its path calls the GraphQL API.

The rule, in one sentence:

    Inside ``rekuest/``, only ``rekuest/api/``, ``rekuest/client/`` and
    ``rekuest/arkitekt.py`` may name ``rekuest.api.schema``; every other module
    speaks the protocol vocabulary, and importing ``rekuest`` must not load the
    client surface at all.

Until the generated module is split in two, the first assertion is the one that
bites; the rest come with the split and are written so they cannot pass on the
leaky state they exist to catch.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import rekuest

PACKAGE = Path(rekuest.__file__).parent

#: The client layer: hand-written GraphQL, generated GraphQL, and the one module
#: that wires the client half to the socket half.
#:
#: ``traits/action.py`` belongs here despite its address. It is the ``Callable``
#: mixin turms weaves into ``Action``, which is a fragment -- so it is a piece of
#: the client surface that happens to live upstream of the generated module,
#: which is why it can only reach the client from inside a function body.
CLIENT_PACKAGES = {PACKAGE / "client", PACKAGE / "api"}
CLIENT_MODULES = {PACKAGE / "arkitekt.py", PACKAGE / "traits" / "action.py"}

#: Where the split puts the protocol vocabulary. Absent until then.
PROTOCOL_MODULE = PACKAGE / "protocol" / "schema.py"
CLIENT_SCHEMA_MODULE = PACKAGE / "api" / "schema.py"


def _all_imports(tree: ast.Module) -> list[str]:
    """Every module named by an import, wherever it sits.

    Deliberately unlike ``test_package_scope._module_scope_imports``, which
    excludes ``TYPE_CHECKING`` blocks and function bodies: a layering rule is
    about what a module is *written against*, and an annotation-only import of a
    fragment is exactly the leak this is looking for.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
    return found


def _imported_names_from(tree: ast.Module, module: str) -> set[str]:
    """The names a module pulls out of ``module``, by any import form."""
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def _defines(path: Path) -> set[str]:
    """The names a module *defines* at module scope -- never the ones it imports.

    This distinction is the whole point. A generated ``api/schema.py`` that does
    ``from rekuest.protocol.schema import ArgPortInput`` makes that name a module
    attribute of ``rekuest.api.schema``, so a naive name set would find it in both
    modules and report the two as overlapping while the re-export back door stood
    wide open.
    """
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _is_client_layer(path: Path) -> bool:
    return path in CLIENT_MODULES or any(
        parent in CLIENT_PACKAGES for parent in path.parents
    )


def _agnostic_modules() -> list[Path]:
    return [p for p in sorted(PACKAGE.rglob("*.py")) if not _is_client_layer(p)]


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #


def test_nothing_outside_the_client_layer_imports_it() -> None:
    """The half of the rule that holds today, before the generated module is split.

    A module that needs a rath, a postman or an operation is a client module. The
    agnostic runtime reaches none of them -- not even for a type annotation.
    """
    offenders = {}
    for path in _agnostic_modules():
        reached = sorted(
            name
            for name in _all_imports(ast.parse(path.read_text()))
            if name == "rekuest.client" or name.startswith("rekuest.client.")
        )
        if reached:
            offenders[str(path.relative_to(PACKAGE))] = reached

    assert not offenders, (
        f"{offenders} import the client layer. Nothing outside rekuest/client/ may: "
        "an agent registers by connecting, and nothing in its path calls the rekuest "
        "GraphQL API. Take a Postman, a StructureClient or a plain id instead."
    )


def test_only_the_client_layer_names_the_generated_client_surface() -> None:
    """The other half: it needs the generated module split first.

    Until then ``rekuest.api.schema`` holds the wire vocabulary too, so every
    module that describes a port names it and the rule cannot be met.
    """
    if not PROTOCOL_MODULE.exists():
        pytest.skip("the generated module is not split yet")

    offenders = [
        str(path.relative_to(PACKAGE))
        for path in _agnostic_modules()
        if "rekuest.api.schema" in _all_imports(ast.parse(path.read_text()))
    ]

    assert not offenders, (
        f"{offenders} name rekuest.api.schema, which holds the fragments, the "
        "operations and the RekuestApi mixin. The wire vocabulary is "
        "rekuest.protocol.schema. A module that genuinely needs a fragment or an "
        "operation belongs in rekuest/client/."
    )


def test_the_guard_sees_a_type_checking_only_leak() -> None:
    """The check itself. An annotation-only import is still a dependency."""
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from rekuest.api.schema import Action\n"
        "def later():\n"
        "    from rekuest.api.schema import Implementation\n"
    )
    assert _all_imports(ast.parse(source)) == [
        "typing",
        "rekuest.api.schema",
        "rekuest.api.schema",
    ]


# --------------------------------------------------------------------------- #
# What `import rekuest` costs
# --------------------------------------------------------------------------- #


def test_importing_rekuest_does_not_load_the_client_surface() -> None:
    """Has to be a subprocess: ``tests/conftest.py`` imports the client, so by the
    time this runs in-process ``rekuest.api.schema`` is already in ``sys.modules``
    and the assertion could never fail.
    """
    if not PROTOCOL_MODULE.exists():
        pytest.skip("the generated module is not split yet")

    code = (
        "import sys, rekuest; "
        "print(sorted(m for m in sys.modules if m.startswith('rekuest.')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env=os.environ,
    )
    loaded = ast.literal_eval(result.stdout.strip())

    assert "rekuest.api.schema" not in loaded, (
        "importing rekuest loads the whole GraphQL client surface. The declaration "
        "surface should reach rekuest.protocol.schema and stop there."
    )
    assert "rekuest.protocol.schema" in loaded


# --------------------------------------------------------------------------- #
# No accidental re-export
# --------------------------------------------------------------------------- #


def test_the_two_generated_modules_define_disjoint_names() -> None:
    """One definition each, or pydantic builds every input model twice."""
    if not PROTOCOL_MODULE.exists():
        pytest.skip("the generated module is not split yet")

    overlap = _defines(PROTOCOL_MODULE) & _defines(CLIENT_SCHEMA_MODULE)

    assert not overlap, (
        f"{sorted(overlap)} are defined in both generated modules. The api project "
        "should be importing them from the protocol project, not regenerating them."
    )


def test_no_protocol_name_is_reached_through_the_client_module() -> None:
    """The client module re-exports what it imports. Nobody may lean on that.

    ``api/schema.py`` does ``from rekuest.protocol.schema import ArgPortInput``,
    which makes ``rekuest.api.schema.ArgPortInput`` resolve. That is a shim by
    another name, and this is what stops one appearing.
    """
    if not PROTOCOL_MODULE.exists():
        pytest.skip("the generated module is not split yet")

    protocol_names = _defines(PROTOCOL_MODULE)
    offenders: dict[str, list[str]] = {}
    for path in _agnostic_modules():
        borrowed = (
            _imported_names_from(ast.parse(path.read_text()), "rekuest.api.schema")
            & protocol_names
        )
        if borrowed:
            offenders[str(path.relative_to(PACKAGE))] = sorted(borrowed)

    assert not offenders, (
        f"{offenders} take protocol names out of rekuest.api.schema, which only "
        "re-exports them. Import them from rekuest.protocol.schema."
    )
