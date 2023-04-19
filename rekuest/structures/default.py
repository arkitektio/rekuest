from rekuest.structures.registry import StructureRegistry
from rekuest.api.schema import (
    TemplateFragment,
    NodeFragment,
    aget_template,
    afind,
    Scope,
)


DEFAULT_STRUCTURE_REGISTRY = None


def get_default_structure_registry() -> StructureRegistry:
    global DEFAULT_STRUCTURE_REGISTRY
    if not DEFAULT_STRUCTURE_REGISTRY:
        DEFAULT_STRUCTURE_REGISTRY = StructureRegistry()

        DEFAULT_STRUCTURE_REGISTRY.register_as_structure(
            TemplateFragment,
            "@rekuest/template",
            scope=Scope.GLOBAL,
            expand=aget_template,
        )
        DEFAULT_STRUCTURE_REGISTRY.register_as_structure(
            NodeFragment, "@rekuest/node", scope=Scope.GLOBAL, expand=afind
        )

        try:
            from .annotations import add_annotations_to_structure_registry

            add_annotations_to_structure_registry(DEFAULT_STRUCTURE_REGISTRY)
        except ImportError:
            # annotations are not installed, either because annotated types
            # is not installed or because python is lower than 3.9 (Annotated
            # types got introduced in python 3.9)
            pass

    return DEFAULT_STRUCTURE_REGISTRY
