"""Edge cases in defining and (de)serializing primitive ports."""

from enum import Enum
from typing import Literal

import pytest

from rekuest.actors.types import Shelver
from rekuest.annotations.parsers import PortAnnotations, extract_annotations
from rekuest.definition.define import prepare_definition
from rekuest.definition.hash import hash_definition
from rekuest.protocol.schema import ActionKind, PortKind
from rekuest.structures.registry import StructureRegistry
from rekuest.structures.serialization.actor import shrink_outputs
from rekuest.structures.serialization.expand import aexpand_return
from rekuest.structures.serialization.predication import predicate_port
from rekuest.structures.serialization.shrink import ashrink_arg


def float_with_int_default(x: float = 1) -> float:
    """Float"""
    return x


def list_with_empty_default(x: list[int] = []) -> int:  # noqa: B006
    """List"""
    return len(x)


def takes_float(x: float) -> float:
    """Float"""
    return x


def takes_literal(x: Literal[1, 2]) -> int:
    """Literal"""
    return x


def maybe_nothing() -> int | None:
    """Maybe"""
    return None


def returns_int(x: int) -> int:
    """Same"""
    return x


class Color(str, Enum):
    RED = "red"


def str_enum_with_default(x: Color = Color.RED) -> str:
    """Enum"""
    return x


def test_annotation_decides_over_default_type() -> None:
    definition = prepare_definition(float_with_int_default, StructureRegistry())
    assert definition.args[0].kind == PortKind.FLOAT


def test_enum_with_default_stays_an_enum() -> None:
    definition = prepare_definition(str_enum_with_default, StructureRegistry())
    assert definition.args[0].kind == PortKind.ENUM


def test_empty_list_default_is_kept() -> None:
    definition = prepare_definition(list_with_empty_default, StructureRegistry())
    assert definition.args[0].default == []


@pytest.mark.asyncio
async def test_float_expands_json_ints() -> None:
    registry = StructureRegistry()
    definition = prepare_definition(takes_float, registry)
    assert await aexpand_return(definition.args[0], 3, registry) == 3.0


def test_union_predicates_keep_bool_and_int_apart() -> None:
    registry = StructureRegistry()
    float_port = prepare_definition(takes_float, registry).args[0]
    int_port = prepare_definition(returns_int, registry).args[0]
    assert not predicate_port(int_port, True, registry)
    assert predicate_port(int_port, 3, registry)
    assert predicate_port(float_port, 3, registry)
    assert not predicate_port(float_port, True, registry)


@pytest.mark.asyncio
async def test_int_literal_shrinks_on_the_client() -> None:
    registry = StructureRegistry()
    definition = prepare_definition(takes_literal, registry)
    assert await ashrink_arg(definition.args[0], 1, registry) == "1"


@pytest.mark.asyncio
async def test_single_nullable_return_of_none(mock_shelver: Shelver) -> None:
    registry = StructureRegistry()
    definition = prepare_definition(maybe_nothing, registry)
    assert await shrink_outputs(definition, None, registry, mock_shelver) == {"return0": None}


def test_hash_tells_functions_and_generators_apart() -> None:
    registry = StructureRegistry()
    function = prepare_definition(returns_int, registry)
    generator = function.model_copy(update={"kind": ActionKind.GENERATOR})
    assert hash_definition(function) != hash_definition(generator)


def test_annotation_parsing_leaves_callers_lists_alone() -> None:
    validators: list = []
    effects: list = []
    extract_annotations([], PortAnnotations(validators=validators, effects=effects))
    base = PortAnnotations(validators=validators, effects=effects)
    result = extract_annotations([], base)
    assert result.validators is not validators and result.effects is not effects


def takes_flag(x: bool) -> bool:
    """Flag"""
    return x


@pytest.mark.asyncio
@pytest.mark.parametrize("wire", ["false", "0", 1, True])
async def test_bool_expands_with_python_truthiness(wire: object) -> None:
    registry = StructureRegistry()
    definition = prepare_definition(takes_flag, registry)
    assert await aexpand_return(definition.args[0], wire, registry) is bool(wire)
