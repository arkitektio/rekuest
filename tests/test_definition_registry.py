from rekuest.definition.define import prepare_definition
from rekuest.definition.registry import DefinitionRegistry


def test_register_function(simple_registry):
    defi = DefinitionRegistry(structure_registry=simple_registry)

    def func():
        """This function

        This function is a test function

        """

        return 1

    defi.register(func, simple_registry)
    x = prepare_definition(func, simple_registry)

    assert defi.definitions[x]
