from rekuest.structures.registry import StructureRegistry
from rekuest.api.schema import (
    TemplateFragment,
    NodeFragment,
    Search_templatesQuery,
    Search_nodesQuery,
    Search_testcasesQuery,
    Search_testresultsQuery,
    TestCaseFragment,
    TestResultFragment,
    aget_template,
    aget_testcase,
    aget_testresult,
    aget_template,
    afind,
    Scope,
)
from rekuest.widgets import SearchWidget


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
            default_widget=SearchWidget(
                query=Search_templatesQuery.Meta.document, ward="rekuest"
            ),
        )

        DEFAULT_STRUCTURE_REGISTRY.register_as_structure(
            NodeFragment,
            "@rekuest/node",
            scope=Scope.GLOBAL,
            expand=afind,
            default_widget=SearchWidget(
                query=Search_nodesQuery.Meta.document, ward="rekuest"
            ),
        )

        DEFAULT_STRUCTURE_REGISTRY.register_as_structure(
            TestCaseFragment,
            "@rekuest/testcase",
            scope=Scope.GLOBAL,
            expand=aget_testcase,
            default_widget=SearchWidget(
                query=Search_testcasesQuery.Meta.document, ward="rekuest"
            ),
        )

        DEFAULT_STRUCTURE_REGISTRY.register_as_structure(
            TestResultFragment,
            "@rekuest/testresult",
            scope=Scope.GLOBAL,
            expand=aget_testresult,
            default_widget=SearchWidget(
                query=Search_testresultsQuery.Meta.document, ward="rekuest"
            ),
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
