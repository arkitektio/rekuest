from typing import Any, Dict, List, Optional, Tuple, Union
from rekuest.api.schema import NodeFragment
import asyncio
from rekuest.structures.errors import ExpandingError, ShrinkingError
from rekuest.structures.registry import StructureRegistry
from rekuest.api.schema import (
    PortFragment,
    PortKind,
    DefinitionInput,
    DefinitionFragment,
    ChildPortFragment,
)
from rekuest.structures.errors import (
    PortShrinkingError,
    StructureShrinkingError,
    PortExpandingError,
    StructureExpandingError,
)


def predicate_port(port: Union[PortFragment, ChildPortFragment], value: Any, structure_registry=None):
    if port.kind == PortKind.DICT:
        if not isinstance(value, dict): return False
        return all([predicate_port(port.child, value) for key, value in value.items()])
    if port.kind == PortKind.LIST:
        if not isinstance(value, list): return False
        return all([predicate_port(port.child, value) for value in value])
    if port.kind == PortKind.BOOL:
        return isinstance(value, bool)
    if port.kind == PortKind.INT:
        return isinstance(value, int)
    if port.kind == PortKind.FLOAT:
        return isinstance(value, float)
    if port.kind == PortKind.STRUCTURE: 
        predicate = structure_registry.get_predicater_for_identifier(
                        port.identifier
                    )
        return predicate(value)

