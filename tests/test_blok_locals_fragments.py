"""A blok's locals are nodes of its tree; they come back as the tree resolves them: each with the
node it was declared by and its kind always set, on a blok, a revision and an edit's dry run alike."""

from rekuest.api.schema import BlokRevision, LocalKind, ResolvedBlokLocal

RESOLVED = {"node": "Blok[0]/local[0]", "key": "zoom", "kind": "INT", "nullable": False, "persist": False, "default": 2}


def test_a_resolved_local_names_its_node_and_always_has_its_kind() -> None:
    local = ResolvedBlokLocal.model_validate(RESOLVED)
    assert (local.node, local.kind) == ("Blok[0]/local[0]", LocalKind.INT)


def test_a_revision_carries_its_resolved_locals() -> None:
    revision = BlokRevision.model_validate(
        {
            "id": "1",
            "number": 1,
            "createdAt": "2026-10-25T00:00:00Z",
            "vocab": 2,
            "catalogs": [],
            "resolvedLocals": [RESOLVED],
        }
    )
    assert [local.node for local in revision.resolved_locals] == ["Blok[0]/local[0]"]
