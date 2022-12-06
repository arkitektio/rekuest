from rekuest.scalars import Identifier, SearchQuery
import pydantic
from pydantic import ValidationError
import pytest


class Serializing(pydantic.BaseModel):
    identifier: Identifier

    class Config:
        arbitrary_types_allowed = True


def test_identifier():

    Serializing(identifier="hm/test")


def test_identifier():
    with pytest.raises(ValidationError):
        Serializing(identifier="@dffest")
