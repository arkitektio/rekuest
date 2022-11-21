from graphql import OperationType
from pydantic import Field
from rath import rath
import contextvars
from rath.links.aiohttp import AIOHttpLink
from rath.links.auth import AuthTokenLink

from rath.links.base import TerminatingLink
from rath.links.compose import TypedComposedLink, compose
from rath.links.dictinglink import DictingLink
from rath.links.shrink import ShrinkingLink
from rath.links.split import SplitLink
from rath.links.retry import RetryLink
from rath.links.websockets import WebSocketLink

current_rekuest_rath = contextvars.ContextVar("current_rekuest_rath", default=None)


class RekuestLinkComposition(TypedComposedLink):
    shrink: ShrinkingLink = Field(default_factory=ShrinkingLink)
    dicting: DictingLink = Field(default_factory=DictingLink)
    auth: AuthTokenLink
    retry: RetryLink = Field(default_factory=RetryLink)
    split: SplitLink


class RekuestRath(rath.Rath):
    link: RekuestLinkComposition = Field(default_factory=RekuestLinkComposition)

    async def __aenter__(self):
        await super().__aenter__()
        current_rekuest_rath.set(self)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await super().__aexit__(exc_type, exc_val, exc_tb)
        current_rekuest_rath.set(None)
