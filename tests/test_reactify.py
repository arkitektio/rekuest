from rekuest.actors.actify import reactify
from rekuest.definition.define import prepare_definition


def test_actify_function(simple_registry):
    def func():
        """This function

        This function is a test function

        """

        return 1

    defi, actorBuilder = reactify(func, simple_registry)



def test_actify_generator(simple_registry):
    def gen():
        """This function

        This function is a test function

        """

        yield 1

    defi, actorBuilder = reactify(gen, simple_registry)
