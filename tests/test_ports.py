from rekuest.api.schema import ArgPortInput, PortKind, ChildPortInput
from pydantic import ValidationError
import pytest


def test_argport_input_errors():

    with pytest.raises(ValidationError):
        # kind is required and only accepts PortKind
        ArgPortInput(kind="lala")

    with pytest.raises(ValidationError):
        # key and nullable are required
        ArgPortInput(kind=PortKind.BOOL)

    with pytest.raises(ValidationError):
        # nullable is required
        ArgPortInput(kind=PortKind.BOOL, key="search")

    with pytest.raises(ValidationError):
        # identifier is required for STRUCTURE
        ArgPortInput(kind=PortKind.STRUCTURE, key="search", nullable=False)

    with pytest.raises(ValidationError):
        # child is required for List
        ArgPortInput(kind=PortKind.LIST, key="search", nullable=False)


def test_argport():

    ArgPortInput(kind=PortKind.BOOL, key="search", nullable=False)
    ArgPortInput(kind=PortKind.STRING, key="search", nullable=False)

    ArgPortInput(
        kind=PortKind.STRUCTURE, identifier="hm/karl", key="search", nullable=False
    )

    ArgPortInput(
        kind=PortKind.LIST,
        child=ChildPortInput(kind=PortKind.BOOL, nullable=False),
        nullable=False,
        key="search",
    )
