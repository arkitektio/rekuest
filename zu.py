from typing import Annotated
from annotated_types import Le, Predicate
from rekuest.definition.define import prepare_definition
from rekuest.structures.builder import StructureRegistryBuilder


builder = StructureRegistryBuilder()
builder.with_default_annotations()

registry = builder.build()

query = """query search($search: x) {
            options: peter {
                value: x
                label: x
            }

    }
    """


def annotated_basic_function(
    rep: Annotated[str, Predicate(str.islower)], name: Annotated[int, Le(3)] = None
) -> str:
    """Karl

    Karl takes a a representation and does magic stuff

    Args:
        rep (str): Nougat
        name (str, optional): Bugat

    Returns:
        Representation: The Returned Representation
    """
    return "tested"


definition = prepare_definition(annotated_basic_function, structure_registry=registry)
print(definition)
# x = DefinitionInput(
#     name="Karl",
#     description="Karl",
#     args=[
#         ArgPortInput(
#             key="a",
#             kind=PortKind.INT,
#             nullable=False,
#             default=0,
#             annotations=(
#                 AnnotationInput(kind=AnnotationKind.ValueRange, max=6, min=1),
#             ),
#             widget=WidgetInput(kind=WidgetKind.SearchWidget, query=query),
#         ),
#         ArgPortInput(
#             key="a",
#             kind=PortKind.STRUCTURE,
#             identifier="sioisoins",
#             nullable=False,
#             default=0,
#             annotations=(
#                 AnnotationInput(kind=AnnotationKind.ValueRange, max=6, min=1),
#             ),
#             widget=WidgetInput(kind=WidgetKind.SearchWidget, query=query),
#         ),
#     ],
#     kind=NodeKindInput.FUNCTION,
# )
