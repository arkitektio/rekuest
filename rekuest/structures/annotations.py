from pydantic import BaseModel
from rekuest.api.schema import AnnotationInput


class ValueRange:
    min: int
    max: int

    def __init__(self, min, max) -> None:
        self.min = min
        self.max = max


def convert_value_range(i: ValueRange):
    return AnnotationInput(kind="ValueRange", min=i.min, max=i.max)
