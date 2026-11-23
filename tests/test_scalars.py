"""Check the logic of the Identifier scalar"""

import pydantic
import pytest
from pydantic import ValidationError

from rekuest.scalars import Identifier


class Serializing(pydantic.BaseModel):
    """Test class for the Identifier scalar"""

    identifier: Identifier


def test_identifier() -> None:
    """Test if the Identifier scalar works as expected"""
    Serializing(identifier="hm/test")


def test_wrong_identifier() -> None:
    """Test if the Identifier scalar works as expected"""
    with pytest.raises(ValidationError):
        Serializing(identifier="@dffest")
