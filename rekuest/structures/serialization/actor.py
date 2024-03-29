from typing import Any, List, Tuple
import asyncio
from rekuest.structures.errors import ExpandingError, ShrinkingError
from rekuest.structures.registry import StructureRegistry
import asyncio
from typing import Any, Union
from rekuest.api.schema import (
    PortFragment,
    PortKind,
    ChildPortFragment,
    DefinitionInput,
    DefinitionFragment,
)
from rekuest.structures.errors import (
    PortShrinkingError,
    StructureShrinkingError,
    StructureExpandingError,
)
from rekuest.definition.validate import auto_validate
from .predication import predicate_port
import datetime as dt


async def aexpand_arg(
    port: Union[PortFragment, ChildPortFragment],
    value: Union[str, int, float, dict, list],
    structure_registry,
) -> Any:
    """Expand a value through a port

    Args:
        port (ArgPortFragment): Port to expand to
        value (Any): Value to expand
    Returns:
        Any: Expanded value

    """
    if value is None:
        value = port.default

    if value is None:
        if port.nullable:
            return None
        else:
            raise ExpandingError(
                f"{port.key} is not nullable (optional) but received None"
            )

    if not isinstance(value, (str, int, float, dict, list)):
        raise ExpandingError(
            f"Can't expand {value} of type {type(value)} to {port.kind}. We only accept"
            " strings, ints and floats (json serializable) and null values"
        ) from None

    if port.kind == PortKind.DICT:
        if not isinstance(value, dict):
            raise ExpandingError(
                f"Can't expand {value} of type {type(value)} to {port.kind}. We only"
                " accept dicts"
            ) from None

        return {
            key: await aexpand_arg(port.child, value, structure_registry)
            for key, value in value.items()
        }

    if port.kind == PortKind.UNION:
        if not isinstance(value, dict):
            raise ExpandingError(
                f"Can't expand {value} of type {type(value)} to {port.kind}. We only"
                " accept dicts in unions"
            )
        assert "use" in value, "No use in vaalue"
        index = value["use"]
        true_value = value["value"]
        return await aexpand_arg(
            port.variants[index], true_value, structure_registry=structure_registry
        )

    if port.kind == PortKind.LIST:
        if not isinstance(value, list):
            raise ExpandingError(
                f"Can't expand {value} of type {type(value)} to {port.kind}. Only"
                " accept lists"
            ) from None

        return await asyncio.gather(
            *[aexpand_arg(port.child, item, structure_registry) for item in value]
        )

    if port.kind == PortKind.INT:
        return int(value)

    if port.kind == PortKind.DATE:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))

    if port.kind == PortKind.FLOAT:
        return float(value)

    if port.kind == PortKind.STRUCTURE:
        try:
            expander = structure_registry.get_expander_for_identifier(port.identifier)
        except KeyError:
            raise StructureExpandingError(
                f"Couldn't find expander for {port.identifier}"
            ) from None

        try:
            expand = await expander(value)
            return expand
        except Exception as e:
            raise StructureExpandingError(
                f"Error expanding {repr(value)} with Structure {port.identifier}"
            ) from e

    if port.kind == PortKind.BOOL:
        return bool(value)

    if port.kind == PortKind.STRING:
        return str(value)

    raise NotImplementedError("Should be implemented by subclass")


async def expand_inputs(
    definition: Union[DefinitionInput, DefinitionFragment],
    args: List[Union[str, int, float, dict, list]],
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
    node = (
        auto_validate(definition)
        if isinstance(definition, DefinitionInput)
        else definition
    )

    if not skip_expanding:
        try:
            expanded_args = await asyncio.gather(
                *[
                    aexpand_arg(port, arg, structure_registry)
                    for port, arg in zip(node.args, args)
                ]
            )

            expandend_params = {
                port.key: val for port, val in zip(node.args, expanded_args)
            }

        except Exception as e:
            raise ExpandingError(f"Couldn't expand Arguments {args}") from e
    else:
        expandend_params = {
            port.key: arg for port, arg in zip(node.args, args) if arg is not None
        }

    return expandend_params


async def ashrink_return(
    port: Union[PortFragment, ChildPortFragment],
    value: Any,
    structure_registry=None,
) -> Union[str, int, float, dict, list, None]:
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
                    f"{port} is not nullable (optional) but your provided None"
                )

        if port.kind == PortKind.UNION:
            for index, x in enumerate(port.variants):
                if predicate_port(x, value, structure_registry):
                    return {
                        "use": index,
                        "value": await ashrink_return(x, value, structure_registry),
                    }

            raise ShrinkingError(
                f"Port is union butn none of the predicated for this port held true {port.variants}"
            )

        if port.kind == PortKind.DICT:
            return {
                key: await ashrink_return(port.child, value, structure_registry)
                for key, value in value.items()
            }

        if port.kind == PortKind.LIST:
            return await asyncio.gather(
                *[
                    ashrink_return(
                        port.child, item, structure_registry=structure_registry
                    )
                    for item in value
                ]
            )

        if port.kind == PortKind.INT:
            return int(value) if value is not None else None

        if port.kind == PortKind.FLOAT:
            return float(value) if value is not None else None

        if port.kind == PortKind.DATE:
            return value.isoformat() if value is not None else None

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
            except Exception as e:
                raise StructureShrinkingError(
                    f"Error shrinking {repr(value)} with Structure {port.identifier}"
                ) from e

        if port.kind == PortKind.BOOL:
            return bool(value) if value is not None else None

        if port.kind == PortKind.STRING:
            return str(value) if value is not None else None

        raise NotImplementedError(f"Should be implemented by subclass {port}")

    except Exception as e:
        raise PortShrinkingError(
            f"Couldn't shrink value {value} with port {port}"
        ) from e


async def shrink_outputs(
    definition: Union[DefinitionInput, DefinitionFragment],
    returns: List[Any],
    structure_registry: StructureRegistry,
    skip_shrinking: bool = False,
) -> Tuple[Union[str, int, float, dict, list, None]]:
    node = (
        auto_validate(definition)
        if isinstance(definition, DefinitionInput)
        else definition
    )

    if returns is None:
        returns = []
    elif not isinstance(returns, tuple):
        returns = [returns]

    assert len(node.returns) == len(
        returns
    ), (  # We are dealing with a single output, convert it to a proper port like structure
        f"Mismatch in Return Length: expected {len(node.returns)} got {len(returns)}"
    )

    if not skip_shrinking:
        shrinked_returns_future = [
            ashrink_return(port, val, structure_registry)
            for port, val in zip(node.returns, returns)
        ]
        try:
            return tuple(await asyncio.gather(*shrinked_returns_future))
        except Exception as e:
            raise ShrinkingError(f"Couldn't shrink Returns {returns}") from e
    else:
        return tuple(val for port, val in zip(node.returns, returns))
