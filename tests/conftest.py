import pytest
from .structures import SerializableObject, SecondSerializableObject, GlobalObject
from rekuest.structures.registry import StructureRegistry, register_structure, Scope

async def mock_shrink():
    return 



@pytest.fixture(scope="module")
def simple_registry():
    registry = StructureRegistry()

    registry.register_as_structure(SerializableObject, identifier="x", scope=Scope.LOCAL)
    registry.register_as_structure(SecondSerializableObject, identifier="x", scope=Scope.LOCAL )

    return registry
