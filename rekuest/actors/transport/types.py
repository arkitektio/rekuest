import asyncio
import logging
from typing import Any, Dict, List, Optional, Protocol, Union, runtime_checkable

from pydantic import BaseModel, Field, PrivateAttr

from koil.types import Contextual
from rekuest.actors.errors import UnknownMessageError
from rekuest.agents.transport.base import AgentTransport
from rekuest.api.schema import (
    AssignationLogLevel,
    AssignationStatus,
    LogLevelInput,
    ProvisionLogLevel,
    ProvisionMode,
    ProvisionStatus,
)
from rekuest.definition.define import DefinitionInput
from rekuest.messages import Assignation, Provision, Unassignation
from rekuest.structures.registry import (
    StructureRegistry,
)


@runtime_checkable
class ActorTransport(Protocol):
    async def change_provision(
        self,
        id: str,
        status: ProvisionStatus = None,
        message: str = None,
        mode: ProvisionMode = None,
    ):
        ...

    async def change_assignation(
        self,
        id: str,
        status: AssignationStatus = None,
        message: str = None,
        returns: List[Any] = None,
        progress: int = None,
    ):
        ...

    async def log_to_provision(
        self,
        id: str,
        level: LogLevelInput = None,
        message: str = None,
    ):
        ...

    async def log_to_assignation(
        self,
        id: str,
        level: LogLevelInput = None,
        message: str = None,
    ):
        ...
