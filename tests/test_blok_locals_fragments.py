"""A blok's locals come back twice: as written (a left-out kind is null) and as its tree
resolves them (every kind set), on a blok, a revision and an edit's dry run alike."""

from rekuest.api.schema import BlokLocal, BlokRevision, LocalKind, ResolvedBlokLocal

WRITTEN = {"key": "zoom", "kind": None, "nullable": False, "persist": False, "default": 2}
RESOLVED = {"key": "zoom", "kind": "INT", "nullable": False, "persist": False, "default": 2}


def test_a_written_local_may_leave_its_kind_out() -> None:
    assert BlokLocal.model_validate(WRITTEN).kind is None


def test_a_resolved_local_always_has_its_kind() -> None:
    assert ResolvedBlokLocal.model_validate(RESOLVED).kind == LocalKind.INT


def test_a_revision_carries_both() -> None:
    revision = BlokRevision.model_validate(
        {
            "id": "1",
            "number": 1,
            "createdAt": "2026-10-25T00:00:00Z",
            "vocab": 2,
            "catalogs": [],
            "locals": [WRITTEN],
            "resolvedLocals": [RESOLVED],
        }
    )
    assert [local.kind for local in revision.locals] == [None]
    assert [local.kind for local in revision.resolved_locals] == [LocalKind.INT]
