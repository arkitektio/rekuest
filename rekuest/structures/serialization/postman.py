from typing import Any, Dict, List, Optional
from rekuest.api.schema import NodeFragment
import asyncio
from rekuest.structures.errors import ExpandingError, ShrinkingError
from rekuest.structures.registry import StructureRegistry
from rekuest.structures.serialization.utils import aexpand, ashrink


async def shrink_inputs(
    node: NodeFragment,
    args: List[Any],
    kwargs: Dict[str, Any],
    structure_registry: StructureRegistry,
    skip_shrinking: bool = False,
) -> List[Any]:
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
    args_iterator = iter(args)

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
            shrinked_args_futures.append(ashrink(port, arg, structure_registry))

        try:
            shrinked_args = await asyncio.gather(*shrinked_args_futures)

        except Exception as e:

            for future in shrinked_args_futures:
                future.cancel()

            await asyncio.gather(*shrinked_args_futures, return_exceptions=True)

            raise ShrinkingError(
                f"Couldn't shrink Arguments {args} with {node.args}"
            ) from e

    else:
        shrinked_args = args_list

    return shrinked_args


async def expand_outputs(
    node: NodeFragment,
    returns: List[Any],
    structure_registry: StructureRegistry,
    skip_expanding: bool = False,
) -> Optional[List[Any]]:
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
            f"Missmatch in Return Length. Node requires {len(node.returns)} returns, but got {len(returns)}"
        )

    if not skip_expanding:
        try:
            if len(returns) == 0:
                return None

            returns = await asyncio.gather(
                *[
                    aexpand(port, val, structure_registry)
                    for port, val in zip(node.returns, returns)
                ]
            )

            if len(returns) == 1:
                return returns[0]  # We are dealing with a single output, just cast back
            else:
                return returns
        except Exception as e:
            raise ExpandingError(f"Couldn't expand Returns {returns}") from e

    else:
        if len(returns) == 0:
            return None

        returns = tuple(val for port, val in zip(node.returns, returns))

        if len(returns) == 1:
            return returns[0]  # We are dealing with a single output, just cast back
        else:
            return returns
