from pydantic import BaseModel
from rekuest.api.schema import AnnotationInput, AnnotationKind, IsPredicateType
from annotated_types import (
    Gt,
    Ge,
    Le,
    Lt,
    Interval,
    Predicate,
    MinLen,
    MaxLen,
    Len,
)


class ValueRange:
    min: int
    max: int

    def __init__(self, min, max) -> None:
        self.min = min
        self.max = max


def convert_value_range(i: ValueRange):
    return AnnotationInput(kind="ValueRange", min=i.min, max=i.max)


def convert_interval(i: Interval):
    return AnnotationInput(kind="ValueRange", min=i.ge or i.gt, max=i.lt or i.le)


def convert_predicate(i: Predicate):
    if i.func == str.islower:
        return AnnotationInput(
            kind=AnnotationKind.IsPredicate, predicate=IsPredicateType.LOWER
        )


def convert_min_len(i: MinLen):
    return AnnotationInput(kind="ValueRange", min=i.min_length)


def convert_max_len(i: MaxLen):
    return AnnotationInput(kind="ValueRange", max=i.max_length)


def convert_len(i: Len):
    return AnnotationInput(kind="ValueRange", min=i.min_length, max=i.min_length)


def convert_gt(i: Gt):
    return AnnotationInput(kind="ValueRange", min=i.gt)


def convert_ge(i: Ge):
    return AnnotationInput(kind="ValueRange", min=i.ge)


def convert_le(i: Le):
    return AnnotationInput(kind="ValueRange", max=i.le)


def convert_lt(i: Lt):
    return AnnotationInput(kind="ValueRange", max=i.lt)


def add_annotations_to_structure_registry(structure_reg):
    structure_reg.register_annotation_converter(ValueRange, convert_value_range)
    structure_reg.register_annotation_converter(Interval, convert_interval)
    structure_reg.register_annotation_converter(Predicate, convert_predicate)
    structure_reg.register_annotation_converter(MinLen, convert_min_len)
    structure_reg.register_annotation_converter(MaxLen, convert_max_len)
    structure_reg.register_annotation_converter(Len, convert_len)
    structure_reg.register_annotation_converter(Gt, convert_gt)
    structure_reg.register_annotation_converter(Ge, convert_ge)
    structure_reg.register_annotation_converter(Le, convert_le)
    structure_reg.register_annotation_converter(Lt, convert_lt)
