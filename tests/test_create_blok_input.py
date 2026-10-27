"""A custom blok is checked against what it declares before it is sent. No network."""

import pytest
from pydantic import ValidationError

from arkitekt_spec.actions import BlokLocalInput, BlokQueryInput, ComponentNodeInput, PortKind
from arkitekt_spec.blok.bsx import parse_bsx
from arkitekt_spec.blok.catalogs import default_view
from rekuest.protocol.schema import CreateBlokInput

IMAGES = BlokQueryInput(key="images", service="mikro", document="query Images { images { id } }")
NAME = BlokLocalInput(key="name", kind=PortKind.STRING, default="x")


def _tree() -> ComponentNodeInput:
    return parse_bsx(
        "<Html><Card><Input bind={name} /><Text>{queries.images.value}</Text></Card></Html>",
        view=default_view(),
    )


def test_a_blok_with_its_locals_and_queries_is_accepted() -> None:
    blok = CreateBlokInput(name="images", vocab=2, components=(_tree(),), locals=(NAME,), queries=(IMAGES,))

    assert [local.key for local in blok.locals or ()] == ["name"]
    assert [query.key for query in blok.queries or ()] == ["images"]
    dumped = blok.model_dump(by_alias=True, exclude_none=True)
    assert dumped["vocab"] == 2
    assert dumped["queries"][0]["service"] == "mikro"


def test_a_query_it_does_not_declare_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"query\.unknown"):
        CreateBlokInput(name="images", vocab=2, components=(_tree(),), locals=(NAME,))


def test_a_local_it_does_not_declare_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"local\.unknown"):
        CreateBlokInput(name="images", vocab=2, components=(_tree(),), queries=(IMAGES,))


def test_a_blok_sent_as_its_source_is_checked_too() -> None:
    source = "<Html><Card><Input bind={name} /><Text>{queries.images.value}</Text></Card></Html>"
    CreateBlokInput(name="images", vocab=2, source=source, locals=(NAME,), queries=(IMAGES,))
    with pytest.raises(ValidationError, match=r"query\.unknown"):
        CreateBlokInput(name="images", vocab=2, source=source, locals=(NAME,))


def test_a_source_that_does_not_parse_is_refused_with_its_code() -> None:
    with pytest.raises(ValidationError, match=r"bsx\.unclosed"):
        CreateBlokInput(name="images", vocab=2, source="<Html><Card>")


def test_a_local_without_kind_is_sent_without_one() -> None:
    """The server infers a left-out kind; a filled-in ANY would stop it."""
    untyped = BlokLocalInput(key="name")
    blok = CreateBlokInput(name="images", vocab=2, components=(_tree(),), locals=(untyped,), queries=(IMAGES,))

    [local] = blok.model_dump(by_alias=True, exclude_unset=True)["locals"]
    assert local == {"key": "name"}
