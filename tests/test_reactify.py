from rekuest.actors.actify import reactify
from rekuest.definition.define import prepare_definition


def test_actify_function(simple_registry):
    def func():
        """This function

        This function is a test function

        """

        return 1

    actorBuilder = reactify(func, simple_registry)
    defi = prepare_definition(func, simple_registry)

    actor = actorBuilder

    assert hasattr(actor, "__definition__")
    assert actor.__definition__ == defi


def test_actify_generator(simple_registry):
    def gen():
        """This function

        This function is a test function

        """

        yield 1

    actorBuilder = reactify(gen, simple_registry)
    defi = prepare_definition(gen, simple_registry)

    actor = actorBuilder

    assert hasattr(actor, "__definition__")
    assert actor.__definition__ == defi
