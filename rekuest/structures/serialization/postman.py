from typing import Any, Dict, List, Optional, Tuple, Union
from rekuest.api.schema import NodeFragment
import asyncio
from rekuest.structures.errors import ExpandingError, ShrinkingError
from rekuest.structures.registry import StructureRegistry
from rekuest.api.schema import (
    PortFragment,
    PortKind,
    ChildPortFragment,
)
from rekuest.structures.errors import (
    PortShrinkingError,
    StructureShrinkingError,
    PortExpandingError,
    StructureExpandingError,
)


async def ashrink_arg(
    port: Union[PortFragment, ChildPortFragment], value: Any, structure_registry=None
) -> Any:
    """Expand a value through a port

    Args:
        port (ArgPortFragment): Port to expand to
        value (Any): Value to expand
    Returns:
        Any: Expanded value

    """
    try:
        if value is None:
            if port.nullable:
                return None
            else:
                raise ValueError(
                    "{port} is not nullable (optional) but your provided None"
                )

        if port.kind == PortKind.DICT:
            return {
                key: await ashrink_arg(port.child, value, structure_registry)
                for key, value in value.items()
            }

        if port.kind == PortKind.LIST:
            return await asyncio.gather(
                *[
                    ashrink_arg(port.child, item, structure_registry=structure_registry)
                    for item in value
                ]
            )

        if port.kind == PortKind.INT:
            return int(value) if value is not None else None

        if port.kind == PortKind.STRUCTURE:
            # We always convert structures returns to strings
            try:
                shrinker = structure_registry.get_shrinker_for_identifier(
                    port.identifier
                )
            except KeyError:
                raise StructureShrinkingError(
                    f"Couldn't find shrinker for {port.identifier}"
                ) from None
            try:
                shrink = await shrinker(value)
                return str(shrink)
            except Exception:
                raise StructureShrinkingError(
                    f"Error shrinking {repr(value)} with Structure {port.identifier}"
                ) from None

        if port.kind == PortKind.BOOL:
            return bool(value) if value is not None else None

        if port.kind == PortKind.STRING:
            return str(value) if value is not None else None

        raise NotImplementedError(f"Should be implemented by subclass {port}")

    except Exception as e:
        raise PortShrinkingError(
            f"Couldn't shrink value {value} with port {port}"
        ) from e


async def shrink_inputs(
    node: NodeFragment,
    args: List[Any],
    kwargs: Dict[str, Any],
    structure_registry: StructureRegistry,
    skip_shrinking: bool = False,
) -> Tuple[Any]:
    """Shrinks args and kwargs

    Shrinks the inputs according to the Node Definition

    Args:
        node (Node): The Node

    Raises:
        ShrinkingError: If args are not Shrinkable
        ShrinkingError: If kwargs are not Shrinkable

    Returns:
        Tuple[List[Any], Dict[str, Any]]: Parsed Args as a List, Parsed Kwargs as a dict
    """

    args_list = []
    try:
        args_iterator = iter(args)
    except TypeError:
        raise ShrinkingError(f"Couldn't iterate over args {args}")

    # Extract to Argslist

    for port in node.args:
        try:
            args_list.append(next(args_iterator))
        except StopIteration as e:
            if port.key in kwargs:
                args_list.append(kwargs.get(port.key, None))
            else:
                if port.nullable or port.default is not None:
                    args_list.append(None)
                else:
                    raise ShrinkingError(
                        f"Couldn't find value for nonnunllable port {port.key}"
                    ) from e

    for port, arg in zip(node.args, args_list):
        if arg is None and not port.nullable and port.default is None:
            raise ShrinkingError(
                f"Argument {port.key} is not nullable, but received null"
            )

    if not skip_shrinking:
        shrinked_args_futures = []

        for port, arg in zip(node.args, args_list):
            shrinked_args_futures.append(
                asyncio.create_task(ashrink_arg(port, arg, structure_registry))
            )

        try:
            shrinked_args = await asyncio.gather(*shrinked_args_futures)

        except Exception as e:
            for future in shrinked_args_futures:
                future.cancel()

            await asyncio.gather(*shrinked_args_futures, return_exceptions=True)

            raise ShrinkingError("Couldn't shrink Arguments") from e

    else:
        shrinked_args = args_list

    return tuple(shrinked_args)


async def aexpand_return(
    port: Union[PortFragment, ChildPortFragment],
    value: Any,
    structure_registry=None,
) -> Any:
    """Expand a value through a port

    Args:
        port (ArgPortFragment): Port to expand to
        value (Any): Value to expand
    Returns:
        Any: Expanded value

    """
    if value is None:
        if port.nullable:
            return None
        else:
            raise PortExpandingError(
                f"{port} is not nullable (optional) but your provided None"
            )

    if port.kind == PortKind.DICT:
        return {
            key: await aexpand_return(port.child, value, structure_registry)
            for key, value in value.items()
        }

    if port.kind == PortKind.LIST:
        return await asyncio.gather(
            *[
                aexpand_return(port.child, item, structure_registry=structure_registry)
                for item in value
            ]
        )

    if port.kind == PortKind.INT:
        return int(value)

    if port.kind == PortKind.FLOAT:
        return float(value)

    if port.kind == PortKind.STRUCTURE:
        if not (isinstance(value, str) or isinstance(value, int)):
            raise PortExpandingError(
                f"Expected value to be a string or int, but got {type(value)}"
            )

        try:
            expander = structure_registry.get_expander_for_identifier(port.identifier)
        except KeyError:
            raise StructureExpandingError(
                f"Couldn't find expander for {port.identifier}"
            ) from None

        try:
            return await expander(value)
        except Exception:
            raise StructureExpandingError(
                f"Error expanding {repr(value)} with Structure {port.identifier}"
            ) from None

    if port.kind == PortKind.BOOL:
        return bool(value)

    if port.kind == PortKind.STRING:
        return str(value)

    raise NotImplementedError("Should be implemented by subclass")


async def expand_outputs(
    node: NodeFragment,
    returns: List[Any],
    structure_registry: StructureRegistry,
    skip_expanding: bool = False,
) -> Optional[Tuple[Any]]:
    """Expands Returns

    Expands the Returns according to the Node definition


    Args:
        node (Node): Node definition
        returns (List[any]): The returns

    Raises:
        ExpandingError: if they are not expandable

    Returns:
        List[Any]: The Expanded Returns
    """
    assert returns is not None, "Returns can't be empty"
    if len(node.returns) != len(returns):
        raise ExpandingError(
            f"Missmatch in Return Length. Node requires {len(node.returns)} returns,"
            f" but got {len(returns)}"
        )
    if len(returns) == 0:
        return None

    if not skip_expanding:
        expanded_returns_futures = [
            asyncio.create_task(aexpand_return(port, value, structure_registry))
            for port, value in zip(node.returns, returns)
        ]

        try:
            expanded_returns = await asyncio.gather(*expanded_returns_futures)

        except Exception as e:
            for future in expanded_returns_futures:
                future.cancel()

            await asyncio.gather(*expanded_returns_futures, return_exceptions=True)

            raise ExpandingError("Couldn't expand Returns") from e

    else:
        expanded_returns = returns

    return tuple(expanded_returns)
