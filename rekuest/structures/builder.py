from .registry import StructureRegistry


class StructureRegistryBuilder:
    def __init__(self, registry: StructureRegistry = None) -> None:
        self.registry = registry or StructureRegistry()

    def register(self, structure, identifier, expand=None):
        self.registry.register_as_structure(structure, identifier, expand)
        return self

    def with_default_annotations(self):
        from .annotations import add_annotations_to_structure_registry

        add_annotations_to_structure_registry(self.registry)
        return self

    def build(self):
        return self.registry
