import pytest
from .structures import SerializableObject, SecondSerializableObject
from rekuest.structures.registry import StructureRegistry, register_structure


@pytest.fixture(scope="module")
def simple_registry():
    registry = StructureRegistry()

    register_structure(identifier="hm/test", registry=registry)(SerializableObject)
    register_structure(identifier="hm/karl", registry=registry)(
        SecondSerializableObject
    )

    return registry
