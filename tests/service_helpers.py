"""Declaring a service in a test: the way a client package does, in two lines.

A client class is *a client* only because a registered service returns it, so a
test that wants ``client: FakeClient`` injected declares a service returning
``FakeClient`` on the registry it registers into.
"""

from typing import Annotated, Any

from fakts import Alias, Require

from rekuest.app import AppRegistry
from rekuest.service import Service


def with_client(registry: AppRegistry, cls: type, name: str) -> Service[Any]:
    """Declare a service named ``name`` on ``registry`` whose builder returns ``cls``.

    The builder is never run: the tests hand the agent a fake app that already
    holds the client. What matters is the return annotation.
    """

    def build(alias: Annotated[Alias, Require(f"live.test.{name}")]) -> Any:  # noqa: ANN401
        return cls()

    build.__name__ = name
    build.__annotations__["return"] = cls
    return registry.service()(build)
