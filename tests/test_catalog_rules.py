"""The client's catalog rules: their ids and the one diagnostic level they use."""

from arkitekt_spec.declare.catalogs import RULES, WARNING, Diagnostic

from rekuest.protocol.schema import DiagnosticLevel


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
