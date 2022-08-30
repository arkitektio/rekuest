from typing import Any, Dict, List
from async_timeout import asyncio
from rekuest.api.schema import NodeFragment
from rekuest.structures.errors import ShrinkingError, ExpandingError
from rekuest.structures.registry import StructureRegistry
from rekuest.structures.serialization.utils import aexpand, ashrink


async def expand_inputs(
    node: NodeFragment,
    args: List[Any],
    structure_registry: StructureRegistry,
    skip_expanding: bool = False,
):
    """Expand

    Args:
        node (NodeFragment): [description]
        args (List[Any]): [description]
        kwargs (List[Any]): [description]
        registry (Registry): [description]
    """

    expanded_args = []

    if not skip_expanding:
        try:
            expanded_args = await asyncio.gather(
                *[
                    aexpand(port, arg, structure_registry)
                    for port, arg in zip(node.args, args)
                ]
            )

            expandend_params = {
                port.key: val for port, val in zip(node.args, expanded_args)
            }

        except Exception as e:
            raise ExpandingError(
                f"Couldn't expand Arguments {args} with {node.args}"
            ) from e
    else:
        expandend_params = {port.key: arg for port, arg in zip(node.args, args)}

    return expandend_params


async def shrink_outputs(
    node: NodeFragment,
    returns: List[Any],
    structure_registry: StructureRegistry,
    skip_shrinking: bool = False,
):
    """Expand

    Args:
        node (NodeFragment): [description]
        args (List[Any]): [description]
        kwargs (List[Any]): [description]
        registry (Registry): [description]
    """
    if returns is None:
        returns = ()
    if not isinstance(returns, tuple):
        returns = [returns]
    assert len(node.returns) == len(
        returns
    ), "Missmatch in Return Length"  # We are dealing with a single output, convert it to a proper port like structure

    if not skip_shrinking:
        shrinked_returns_future = [
            ashrink(port, val, structure_registry)
            for port, val in zip(node.returns, returns)
        ]
        try:
            return await asyncio.gather(*shrinked_returns_future)
        except Exception as e:
            raise ShrinkingError(f"Couldn't shrink Returns {returns}") from e
    else:
        return [val for port, val in zip(node.returns, returns)]
