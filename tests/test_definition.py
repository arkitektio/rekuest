from typing import Dict
from rekuest.structures.registry import StructureRegistry, register_structure
from rekuest.api.schema import DefinitionInput, NodeFragment, PortKind, AnnotationKind
import pytest
from .structures import SecondSerializableObject, SerializableObject
from rekuest.definition.define import prepare_definition
from .mocks import MockRequestRath
from rekuest.structures.builder import StructureRegistryBuilder
from typing import Dict, List, Tuple, Annotated
from .structures import SecondSerializableObject, SerializableObject
from annotated_types import Le, Ge, Gt, LowerCase, UpperCase, Predicate, Len
from .funcs import (
    plain_basic_function,
    plain_structure_function,
    nested_basic_function,
    nested_structure_function,
    annotated_basic_function,
    annotated_nested_structure_function,
    null_function,
)
from rekuest.definition.validate import auto_validate
from rekuest.structures.serialization.postman import shrink_inputs, expand_outputs


@pytest.fixture
def simple_registry():

    builder = StructureRegistryBuilder()
    builder.register(SerializableObject, "SerializableObject")
    builder.register(SecondSerializableObject, "SecondSerializableObject")
    builder.with_default_annotations()

    registry = builder.build()

    return registry


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


@pytest.mark.define
def test_define_null(simple_registry):

    functional_definition = prepare_definition(
        null_function, structure_registry=simple_registry
    )
    assert isinstance(
        functional_definition, DefinitionInput
    ), "output is not a definition"
    assert (
        functional_definition.name == "Karl"
    ), "Doesnt conform to standard Naming Scheme"
    assert functional_definition.args[0].nullable, "Should be nullable"


@pytest.mark.define
def test_define_basic(simple_registry):

    functional_definition = prepare_definition(
        plain_basic_function, structure_registry=simple_registry
    )
    assert isinstance(
        functional_definition, DefinitionInput
    ), "output is not a definition"
    assert (
        functional_definition.name == "Karl"
    ), "Doesnt conform to standard Naming Scheme"
    assert (
        functional_definition.args[0].annotations == ()
    ), "Should not have annotations"


def plain_structure_function(
    rep: SerializableObject, name: SerializableObject = None
) -> SecondSerializableObject:
    """Structure Karl

    Karl takes a a representation and does magic stuff

    Args:
        rep (SerializableObject): Nougat
        name (SerializableObject, optional): Bugat

    Returns:
        SecondSerializableObject: The Returned Representation
    """
    return "tested"


@pytest.mark.define
def test_define_structure(simple_registry):

    functional_definition = prepare_definition(
        plain_structure_function, structure_registry=simple_registry
    )
    assert isinstance(
        functional_definition, DefinitionInput
    ), "output is not a definition"
    assert (
        functional_definition.name == "Structure Karl"
    ), "Doesnt conform to standard Naming Scheme"
    assert functional_definition.args[0].identifier == "SerializableObject"


@pytest.mark.define
def test_define_nested_basic_function(simple_registry):

    functional_definition = prepare_definition(
        nested_basic_function, structure_registry=simple_registry
    )
    assert isinstance(
        functional_definition, DefinitionInput
    ), "output is not a definition"
    assert (
        functional_definition.name == "Structure Karl"
    ), "Doesnt conform to standard Naming Scheme"
    assert len(functional_definition.args) == 3, "Wrong amount of Arguments"
    assert (
        functional_definition.args[0].kind == PortKind.LIST
    ), "Wasn't defined as a List"
    assert (
        functional_definition.args[1].kind == PortKind.DICT
    ), "Wasn't defined as a Dict"
    assert (
        functional_definition.args[1].child.kind == PortKind.INT
    ), "Child of List is not of type IntArgPort"
    assert (
        functional_definition.args[0].child.kind == PortKind.STRING
    ), "Child of Dict is not of type StringArgPort"
    assert (
        functional_definition.args[2].kind == PortKind.STRING
    ), "Kwarg wasn't defined as a StringKwargPort"
    assert len(functional_definition.returns) == 2, "Wrong amount of Returns"
    assert (
        functional_definition.returns[0].kind == PortKind.LIST
    ), "Needs to Return List"


def nested_structure_function(
    rep: List[SerializableObject], name: Dict[str, SerializableObject] = None
) -> Tuple[str, Dict[str, SecondSerializableObject]]:
    """Structured Karl

    Naoinaoainao

    Args:
        rep (List[SerializableObject]): [description]
        name (Dict[str, SerializableObject], optional): [description]. Defaults to None.

    Returns:
        str: [description]
        Dict[str, SecondSerializableObject]: [description]
    """
    return "tested"


@pytest.mark.define
def test_define_nested_structure_function(simple_registry):

    functional_definition = prepare_definition(
        nested_structure_function, structure_registry=simple_registry
    )
    assert isinstance(
        functional_definition, DefinitionInput
    ), "output is not a definition"
    assert (
        functional_definition.name == "Structured Karl"
    ), "Doesnt conform to standard Naming Scheme"
    assert len(functional_definition.args) == 2, "Wrong amount of Arguments"
    assert (
        functional_definition.args[0].kind == PortKind.LIST
    ), "Wasn't defined as a List"
    assert (
        functional_definition.args[1].kind == PortKind.DICT
    ), "Wasn't defined as a Dict"
    assert (
        functional_definition.args[0].child.kind == PortKind.STRUCTURE
    ), "Child of List is not of type IntArgPort"
    assert (
        functional_definition.args[0].child.identifier == "SerializableObject"
    ), "Child of List is not of type IntArgPort"
    assert (
        functional_definition.args[0].child.kind == PortKind.STRUCTURE
    ), "Child of Dict is not of type StringArgPort"
    assert len(functional_definition.returns) == 2, "Wrong amount of Returns"
    assert functional_definition.returns[0].kind == PortKind.STRING
    assert functional_definition.returns[1].kind == PortKind.DICT
    assert functional_definition.returns[1].child.kind == PortKind.STRUCTURE
    assert (
        functional_definition.returns[1].child.identifier == "SecondSerializableObject"
    )


@pytest.mark.define
def test_define_annotated_basic_function(simple_registry):

    functional_definition = prepare_definition(
        annotated_basic_function, structure_registry=simple_registry
    )
    assert isinstance(functional_definition, DefinitionInput), "Node is not Node"
    assert (
        functional_definition.name == "Annotated Karl"
    ), "Doesnt conform to standard Naming Scheme"

    assert (
        functional_definition.args[0].annotations[0].kind == AnnotationKind.IsPredicate
    )
    assert (
        functional_definition.args[1].annotations[0].kind == AnnotationKind.ValueRange
    )
    assert functional_definition.args[1].annotations[0].max == 4
    assert functional_definition.args[1].annotations[0].min == None
    assert (
        functional_definition.args[1].annotations[1].kind == AnnotationKind.ValueRange
    )
    assert functional_definition.args[1].annotations[1].max == None
    assert functional_definition.args[1].annotations[1].min == 4


@pytest.mark.define
def test_define_annotated_basic_function(simple_registry):

    functional_definition = prepare_definition(
        annotated_nested_structure_function, structure_registry=simple_registry
    )
    assert isinstance(functional_definition, DefinitionInput), "Node is not Node"
    assert (
        functional_definition.name == "Annotated Karl"
    ), "Doesnt conform to standard Naming Scheme"

    assert (
        functional_definition.args[0].annotations[0].kind == AnnotationKind.IsPredicate
    )
    assert functional_definition.args[1].kind == PortKind.DICT
    assert functional_definition.args[1].child.kind == PortKind.LIST
    assert (
        functional_definition.args[1].child.annotations[0].kind
        == AnnotationKind.ValueRange
    )
    assert functional_definition.args[1].child.annotations[0].max == 3
    assert functional_definition.args[1].child.annotations[0].min == 3
    assert functional_definition.args[1].child.child.kind == PortKind.STRUCTURE
    assert (
        functional_definition.args[1].child.child.identifier
        == "SecondSerializableObject"
    )


@pytest.mark.define
def test_auto_validate(simple_registry):

    functional_definition = prepare_definition(
        annotated_nested_structure_function, structure_registry=simple_registry
    )

    x = auto_validate(functional_definition)


@pytest.fixture
def arkitekt_rath():

    return MockRequestRath()


@pytest.mark.define
@pytest.mark.asyncio
async def test_shrinking(simple_registry):

    functional_definition = prepare_definition(
        plain_basic_function, structure_registry=simple_registry
    )

    definition = auto_validate(functional_definition)

    args = await shrink_inputs(definition, ("hallo", "zz"), {}, simple_registry)
    assert args == ("hallo", "zz")
