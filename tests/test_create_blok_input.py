"""A custom blok is checked against what it declares before it is sent. No network."""

import pytest
from arkitekt_spec.actions import ComponentNodeInput
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.catalogs import default_view
from pydantic import ValidationError

from rekuest.protocol.schema import CreateBlokInput

NAME = '<local name="name" kind="STRING" default={"x"} />'
IMAGES = (
    '<query name="images" service="mikro" document="query Images { images { id } }" />'
)
CARD = "<Card><Input bind={name} /><Text>{queries.images.value}</Text></Card>"


def _source(*declarations: str) -> str:
    return f'<Blok kind="custom" context="panel">{"".join(declarations)}{CARD}</Blok>'


def _tree(*declarations: str) -> ComponentNodeInput:
    return parse_bsx(_source(*declarations), view=default_view())


def _children(tree: ComponentNodeInput) -> list[ComponentNodeInput]:
    return list(tree.children or ())


def test_a_blok_with_its_locals_and_queries_is_accepted() -> None:
    blok = CreateBlokInput(name="images", vocab=2, components=(_tree(NAME, IMAGES),))

    [root] = blok.components or ()
    assert [child.component for child in _children(root)] == ["local", "query", "Card"]
    dumped = blok.model_dump(by_alias=True, exclude_none=True)
    assert dumped["vocab"] == 2
    assert "locals" not in dumped and "queries" not in dumped


def test_a_query_it_does_not_declare_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"query\.unknown"):
        CreateBlokInput(name="images", vocab=2, components=(_tree(NAME),))


def test_a_local_it_does_not_declare_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"local\.unknown"):
        CreateBlokInput(name="images", vocab=2, components=(_tree(IMAGES),))


def test_a_blok_sent_as_its_source_is_checked_too() -> None:
    CreateBlokInput(name="images", vocab=2, source=_source(NAME, IMAGES))
    with pytest.raises(ValidationError, match=r"query\.unknown"):
        CreateBlokInput(name="images", vocab=2, source=_source(NAME))


def test_a_source_that_does_not_parse_is_refused_with_its_code() -> None:
    with pytest.raises(ValidationError, match=r"bsx\.unclosed"):
        CreateBlokInput(
            name="images", vocab=2, source='<Blok kind="custom" context="panel"><Card>'
        )


def test_a_local_without_kind_is_sent_without_one() -> None:
    """The server infers a left-out kind; a filled-in ANY would stop it."""
    blok = CreateBlokInput(
        name="images", vocab=2, components=(_tree('<local name="name" />', IMAGES),)
    )

    [root] = blok.components or ()
    local = _children(root)[0]
    assert local.component == "local"
    assert [prop.key for prop in local.props or ()] == ["name"]
