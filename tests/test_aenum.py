from rekuest.api.schema import DefinitionInput, PortKind, AnnotationKind
import pytest
from .structures import SecondSerializableObject, SerializableObject
from rekuest.definition.define import prepare_definition
from rekuest.structures.registry import StructureRegistry, Scope
from aenum import Enum


class PlainEnum(Enum):
    """Plain Enum"""

    A = "A"
    B = "B"


def plain_enum_function(rep: str, name: PlainEnum = PlainEnum.A) -> str:
    """Karl

    Karl takes a a representation and does magic stuff

    Args:
        rep (str): Nougat
        name (str, optional): Bugat

    Returns:
        Representation: The Returned Representation
    """
    return "tested"


@pytest.fixture
def simple_registry():
    reg = StructureRegistry()
    reg.register_as_structure(
        SerializableObject, "SerializableObject", scope=Scope.LOCAL
    )
    reg.register_as_structure(
        SecondSerializableObject, "SecondSerializableObject", scope=Scope.LOCAL
    )

    return reg


@pytest.mark.define
def assert_definition_hash(simple_registry):
    functional_definition = prepare_definition(
        null_function, structure_registry=simple_registry
    )
    function_two_definition = prepare_definition(
        null_function, structure_registry=simple_registry
    )

    assert hash(functional_definition) == hash(function_two_definition)

    x = {}
    x[functional_definition] = "test"

    assert x[function_two_definition] == "test"
