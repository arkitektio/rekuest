from typing import Dict
from rekuest.structures.registry import StructureRegistry, register_structure
from rekuest.api.schema import DefinitionInput, NodeFragment, PortKind
import pytest
from .funcs import karl, complex_karl, karl_structure, structured_gen
from .structures import SecondSerializableObject, SerializableObject
from rekuest.definition.define import prepare_definition
from .mocks import MockRequestRath


@pytest.fixture
def simple_registry():

    registry = StructureRegistry()

    register_structure(identifier="hm/test", registry=registry)(SerializableObject)
    register_structure(identifier="ha/karl", registry=registry)(
        SecondSerializableObject
    )

    return registry


async def test_define(simple_registry):

    functional_definition = prepare_definition(karl, structure_registry=simple_registry)
    assert isinstance(
        functional_definition, DefinitionInput
    ), "output is not a definition"
    assert (
        functional_definition.name == "Karl"
    ), "Doesnt conform to standard Naming Scheme"


async def test_define_complex(simple_registry):

    functional_definition = prepare_definition(
        complex_karl, structure_registry=simple_registry
    )
    assert isinstance(
        functional_definition, DefinitionInput
    ), "output is not a definition"
    assert (
        functional_definition.name == "Complex Karl"
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


async def test_define_structure(simple_registry):

    functional_definition = prepare_definition(
        karl_structure, structure_registry=simple_registry
    )
    assert isinstance(
        functional_definition, DefinitionInput
    ), "output is not a definition"
    assert (
        functional_definition.name == "Karl"
    ), "Doesnt conform to standard Naming Scheme"


async def test_define_structured_gen(simple_registry):

    functional_definition = prepare_definition(
        structured_gen, structure_registry=simple_registry
    )
    assert isinstance(functional_definition, DefinitionInput), "Node is not Node"
    assert (
        functional_definition.name == "Structured Karl"
    ), "Doesnt conform to standard Naming Scheme"


@pytest.fixture
def arkitekt_rath():

    return MockRequestRath()


async def test_define_to_node_gen(simple_registry, arkitekt_rath):

    functional_definition = prepare_definition(
        structured_gen, structure_registry=simple_registry
    )


async def test_define_to_node_complex(simple_registry, arkitekt_rath):

    functional_definition = prepare_definition(
        complex_karl, structure_registry=simple_registry
    )


async def test_define_node_has_nested_type(simple_registry, arkitekt_rath):
    def x(a: Dict[str, Dict[str, int]]) -> int:
        """Nanana

        sss

        Args:
            a (Dict[str, Dict[str, int]]): _description_
        """
        return 5

    functional_definition = prepare_definition(x, structure_registry=simple_registry)
