"""Per-call context threaded through the kind-dispatch tables."""

from dataclasses import dataclass, field, replace
from typing import Any
from collections.abc import Awaitable, Callable, Sequence

from rath.scalars import ID
from rekuest.actors.types import Shelver
from rekuest.protocol.schema import PortKind
from rekuest.structures.registry import StructureRegistry
from rekuest.structures.serialization.batching import ExpandBatcher
from rekuest.structures.serialization.protocols import SerializablePort
from rekuest.structures.types import FullFilledStructure


@dataclass(frozen=True)
class SerializationContext:
    """What a (de)serialization step needs besides the port and the value.

    ``path``/``depth`` only feed error messages; ``shelver`` is only present on
    the actor side, where memory structures are resolved against the local
    shelve. ``batcher`` collects the structure expansions of this call, so that
    ids expanded together are fetched together; nested contexts share it.

    Which client an expansion uses is not carried here: an assignment runs pinned
    to its agent's app, so the expanders resolve it from what is current.
    """

    registry: StructureRegistry
    shelver: Shelver | None = None
    path: tuple[str, ...] = ()
    depth: int = 0
    batcher: ExpandBatcher = field(default_factory=ExpandBatcher)

    @classmethod
    def build(
        cls,
        registry: StructureRegistry,
        shelver: Shelver | None = None,
        path: Sequence[str] | None = None,
        depth: int = 0,
        batcher: ExpandBatcher | None = None,
    ) -> "SerializationContext":
        return cls(
            registry=registry,
            shelver=shelver,
            path=tuple(path or ()),
            depth=depth,
            batcher=batcher or ExpandBatcher(),
        )

    async def load(self, structure: FullFilledStructure, id: ID) -> Any:  # noqa: ANN401
        """Expand ``id`` as ``structure``, batched with its siblings where it can be."""
        return await self.batcher.load(structure, id)

    def child(self, *parts: str) -> "SerializationContext":
        """Context for a nested port one level down."""
        return replace(self, path=(*self.path, *parts), depth=self.depth + 1)

    def require_shelver(self) -> Shelver:
        if self.shelver is None:
            raise RuntimeError(
                "This serialization step needs a shelver but none was provided"
            )
        return self.shelver


Handler = Callable[[SerializablePort, Any, SerializationContext], Awaitable[Any]]
KindTable = dict[PortKind, Handler]


def single_child(port: SerializablePort) -> SerializablePort | None:
    """The only child of a LIST/DICT port, or ``None`` if the port is malformed."""
    if not port.children or len(port.children) != 1:
        return None
    return port.children[0]


def union_index(value: Any) -> tuple[int | None, str | None]:  # noqa: ANN401
    """Parse a tagged ``{"__use": i, "__value": ...}`` union value.

    Returns ``(index, None)`` on success, ``(None, reason)`` otherwise.
    """
    if not isinstance(value, dict) or "__use" not in value or "__value" not in value:
        return None, (
            "Union value needs to be a tagged "
            '{"__use": index, "__value": ...} dict, got '
            f"{type(value).__name__}"
        )
    index = value["__use"]
    if not isinstance(index, int) or isinstance(index, bool):
        return (
            None,
            f"Union '__use' must be an integer index, got {type(index).__name__}",
        )
    return index, None
