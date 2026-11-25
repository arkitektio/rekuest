"""Using a client in a test, as an assembled app does for the clients it found."""

from arkitekt_spec.declare.app import AppRegistry
from arkitekt_spec.declare.client import client_of


def use(registry: AppRegistry, *classes: type) -> AppRegistry:
    """Use each ``@client`` class on ``registry``: a parameter annotated with it is handed the run's instance."""
    for cls in classes:
        declared = client_of(cls)
        assert declared is not None, f"{cls.__qualname__} is not a @client"
        registry.use_client(declared)
    return registry
