from rekuest.structures.registry import StructureRegistry
from rekuest.api.schema import TemplateFragment, NodeFragment, aget_template, afind
from .annotations import ValueRange, convert_value_range

DEFAULT_STRUCTURE_REGISTRY = None


def get_default_structure_registry() -> StructureRegistry:
    global DEFAULT_STRUCTURE_REGISTRY
    if not DEFAULT_STRUCTURE_REGISTRY:
        DEFAULT_STRUCTURE_REGISTRY = StructureRegistry()

        DEFAULT_STRUCTURE_REGISTRY.register_as_structure(
            TemplateFragment, "@rekuest/template", expand=aget_template
        )
        DEFAULT_STRUCTURE_REGISTRY.register_as_structure(
            NodeFragment, "@rekuest/node", expand=afind
        )

        DEFAULT_STRUCTURE_REGISTRY.register_annotation_converter(
            ValueRange, convert_value_range
        )

    return DEFAULT_STRUCTURE_REGISTRY
