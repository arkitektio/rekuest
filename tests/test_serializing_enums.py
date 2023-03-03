import pytest
from rekuest.definition.define import prepare_definition
from rekuest.definition.validate import auto_validate
from rekuest.structures.serialization.actor import shrink_outputs, expand_inputs
from .funcs import (
    plain_basic_function,
    plain_structure_function,
    nested_basic_function,
    nested_structure_function,
    nested_structure_generator,
    annotated_nested_structure_function,
    null_function,
)
from .structures import SecondObject, SecondSerializableObject, SerializableObject
from .registries import simple_registry
from rekuest.structures.errors import ShrinkingError, ExpandingError
from enum import Enum
from functools import partial
def alert_function_a(x: int):
    return "a"

def alert_function_b(x: int):
    return "b"

def alert_function_c(x: int):
    return "c"



class FunctionEnum(Enum):
    A = partial(alert_function_a)
    B = partial(alert_function_b)
    C = partial(alert_function_c)



def enum_function(x: FunctionEnum) -> str:
    """ Enum function

    Does the enum thing
    
    """
    return x(x)


def enum_function_default(x: FunctionEnum = FunctionEnum.A) -> str:
    """ Enum function

    Does the enum thing
    
    """
    return x(x)




@pytest.mark.expand
@pytest.mark.asyncio
async def test_expand_enums(simple_registry):

    functional_definition = prepare_definition(
        enum_function, structure_registry=simple_registry
    )

    definition = auto_validate(functional_definition)

    args = await expand_inputs(definition, ("C",), simple_registry)
    func =  args["x"]#
    assert func(1) == "c", "Enum function should expand to the correct function"


#@pytest.mark.expand
@pytest.mark.asyncio
async def test_expand_enums_default(simple_registry):

    functional_definition = prepare_definition(
        enum_function_default, structure_registry=simple_registry
    )

    definition = auto_validate(functional_definition)

    args = await expand_inputs(definition, (None,), simple_registry)
    func =  args["x"]#
    assert func(1) == "a", "Enum function should expand to the correct function"
