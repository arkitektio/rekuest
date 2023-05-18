from rekuest.api.schema import DefinitionFragment, DefinitionInput, PortKind, Scope
from typing import List, Any


def parse_collectable(definition: DefinitionInput, returns: List[Any]):
    collectables = []

    if returns is None:
        returns = []
    elif not isinstance(returns, tuple):
        returns = [returns]

    for port, v in zip(definition.returns, returns):
        if port.kind == PortKind.STRUCTURE:
            if port.scope == Scope.LOCAL:
                collectables.append(v)

    return collectables
