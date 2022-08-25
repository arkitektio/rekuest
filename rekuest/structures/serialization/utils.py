import asyncio
from typing import Any
from rekuest.api.schema import ArgPortFragment, PortKind


async def aexpand(port: ArgPortFragment, value: Any, structure_registry=None) -> Any:
    """Expand a value through a port

    Args:
        port (ArgPortFragment): Port to expand to
        value (Any): Value to expand
    Returns:
        Any: Expanded value

    """
    if port.kind == PortKind.DICT:
        return {
            key: await aexpand(port.child, value, structure_registry)
            for key, value in value.items()
        }

    if port.kind == PortKind.LIST:
        return await asyncio.gather(
            *[
                aexpand(port.child, item, structure_registry=structure_registry)
                for item in value
            ]
        )

    if port.kind == PortKind.INT:
        return int(value) if value is not None else int(port.default)

    if port.kind == PortKind.STRUCTURE:
        value = value if value is not None else port.default

        if value is None:
            assert port.nullable, "Null value not allowed for non-nullable port"
            return None

        return await structure_registry.get_expander_for_identifier(port.identifier)(
            value
        )

    if port.kind == PortKind.BOOL:
        if value == None:
            value = port.default

        return bool(value) if value is not None else None

    if port.kind == PortKind.STRING:
        if value == None:
            value = port.default

        return str(value) if value is not None else None

    raise NotImplementedError("Should be implemented by subclass")


async def ashrink(port: ArgPortFragment, value: Any, structure_registry=None) -> Any:
    """Expand a value through a port

    Args:
        port (ArgPortFragment): Port to expand to
        value (Any): Value to expand
    Returns:
        Any: Expanded value

    """
    if port.kind == PortKind.DICT:
        return {
            key: await ashrink(port.child, value, structure_registry)
            for key, value in value.items()
        }

    if port.kind == PortKind.LIST:
        return await asyncio.gather(
            *[
                ashrink(port.child, item, structure_registry=structure_registry)
                for item in value
            ]
        )

    if port.kind == PortKind.INT:
        return int(value) if value is not None else None

    if port.kind == PortKind.STRUCTURE:
        return (
            await structure_registry.get_shrinker_for_identifier(port.identifier)(
                value,
            )
            if value is not None
            else None
        )

    if port.kind == PortKind.BOOL:
        return bool(value) if value is not None else None

    if port.kind == PortKind.STRING:
        return str(value) if value is not None else None

    raise NotImplementedError(f"Should be implemented by subclass {port}")
