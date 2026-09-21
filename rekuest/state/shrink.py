"""Shrink a state using a schema and a structure registry"""

from typing import Any
from rekuest.actors.types import Shelver
from rekuest.protocol.schema import StateDefinitionInput
from rekuest.messages import JSONSerializable
from rekuest.protocol.types import AnyState
from rekuest.structures.registry import StructureRegistry
from rekuest.structures.serialization.actor import ashrink_return


async def ashrink_state(
    state: AnyState,  # noqa: ANN401
    schema: StateDefinitionInput,
    structure_reg: StructureRegistry,  # noqa: ANN401
    shelver: Shelver,
) -> dict[str, Any]:
    """Shrink a state  using a schema and a structure registry

    Args:
        state (Any): The state to shrink
        schema (StateDefinitionInput): The schema to use (defines the ports)
        structure_reg (StructureRegistry): The structure registry to use

    Returns:
        Dict[str, Any]: The shrunk state

    """

    shrinked: dict[str, JSONSerializable] = {}
    for port in schema.ports:
        shrinked[port.key] = await ashrink_return(
            port, getattr(state, port.key), structure_reg, shelver=shelver
        )

    return shrinked
