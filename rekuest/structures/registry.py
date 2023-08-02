import contextvars
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional, Type, TypeVar

from rekuest.api.schema import (
    ChoiceInput,
    ReturnWidgetInput,
    WidgetInput,
    AnnotationInput,
    Scope,
    PortInput,
)
from pydantic import BaseModel, Field
import inspect
from rekuest.collection.shelve import get_current_shelve
from .errors import (
    StructureDefinitionError,
    StructureOverwriteError,
    StructureRegistryError,
)
from .types import PortBuilder

current_structure_registry = contextvars.ContextVar("current_structure_registry")


async def id_shrink(self):
    return self.id


async def shelve_ashrink(cls: Type):
    shelve = get_current_shelve()
    return await shelve.aput(cls)


async def shelve_aexpand(id: str):
    shelve = get_current_shelve()
    return await shelve.aget(id)


async def shelve_acollect(id: str):
    shelve = get_current_shelve()
    return await shelve.adelete(id)


async def void_acollect(id: str):
    return None


def build_instance_predicate(cls: Type):
    return lambda x: isinstance(x, cls)


def build_enum_shrink_expand(cls: Type[Enum]):
    async def shrink(s):
        return s._name_

    async def expand(v):
        return cls.__members__[v].value

    return shrink, expand


T = TypeVar("T")

Identifier = str
""" A unique identifier of this structure on the arkitekt platform"""


class StructureRegistry(BaseModel):
    copy_from_default: bool = False
    allow_overwrites: bool = False
    allow_auto_register: bool = True

    identifier_structure_map: Dict[str, Type] = Field(
        default_factory=dict, exclude=True
    )
    identifier_scope_map: Dict[str, Scope] = Field(default_factory=dict, exclude=True)
    _identifier_expander_map: Dict[str, Callable[[str], Awaitable[Any]]] = {}
    _identifier_shrinker_map: Dict[str, Callable[[Any], Awaitable[str]]] = {}
    _identifier_collecter_map: Dict[str, Callable[[Any], Awaitable[None]]] = {}
    _identifier_predicate_map: Dict[str, Callable[[Any], bool]] = {}
    _identifier_builder_map: Dict[str, PortBuilder] = {}

    _structure_convert_default_map: Dict[str, Callable[[Any], str]] = {}
    _structure_identifier_map: Dict[Type, str] = {}
    _structure_default_widget_map: Dict[Type, WidgetInput] = {}
    _structure_default_returnwidget_map: Dict[Type, ReturnWidgetInput] = {}
    _structure_annotation_map: Dict[Type, Type] = {}

    _token: contextvars.Token = None

    def get_expander_for_identifier(self, key):
        try:
            return self._identifier_expander_map[key]
        except KeyError as e:
            raise StructureRegistryError(f"Expander for {key} is not registered") from e

    def get_collector_for_identifier(self, key):
        try:
            return self._identifier_collecter_map[key]
        except KeyError as e:
            raise StructureRegistryError(
                f"Collector for {key} is not registered"
            ) from e

    def get_shrinker_for_identifier(self, key):
        try:
            return self._identifier_shrinker_map[key]
        except KeyError as e:
            raise StructureRegistryError(f"Shrinker for {key} is not registered") from e

    def register_expander(self, key, expander):
        self._identifier_expander_map[key] = expander

    def get_widget_input(self, cls) -> Optional[WidgetInput]:
        return self._structure_default_widget_map.get(cls, None)

    def get_returnwidget_input(self, cls) -> Optional[ReturnWidgetInput]:
        return self._structure_default_returnwidget_map.get(cls, None)

    def get_predicator_for_identifier(
        self, identifier: str
    ) -> Optional[Callable[[Any], bool]]:
        return self._identifier_predicate_map[identifier]

    def get_identifier_for_structure(self, cls):
        try:
            return self._structure_identifier_map[cls]
        except KeyError as e:
            if self.allow_auto_register:
                try:
                    self.register_as_structure(cls)
                    return self._structure_identifier_map[cls]
                except StructureDefinitionError as e:
                    raise StructureDefinitionError(
                        f"{cls} was not registered and could not be registered"
                        " automatically"
                    ) from e
            else:
                raise StructureRegistryError(
                    f"{cls} is not registered and allow_auto_register is set to False."
                    " Please make sure to register this type beforehand or set"
                    " allow_auto_register to True"
                ) from e

    def get_scope_for_identifier(self, identifier: str):
        return self.identifier_scope_map[identifier]

    def get_default_converter_for_structure(self, cls):
        try:
            return self._structure_convert_default_map[cls]
        except KeyError as e:
            if self.allow_auto_register:
                try:
                    self.register_as_structure(cls)
                    return self._structure_convert_default_map[cls]
                except StructureDefinitionError as e:
                    raise StructureDefinitionError(
                        f"{cls} was not registered and not be no default converter"
                        " could be registered automatically."
                    ) from e
            else:
                raise StructureRegistryError(
                    f"{cls} is not registered and allow_auto_register is set to False."
                    " Please register a 'conver_default' function for this type"
                    " beforehand or set allow_auto_register to True. Otherwise you"
                    " cant use this type with a default"
                ) from e

    def register_as_structure(
        self,
        cls: Type,
        identifier: str = None,
        scope: Scope = Scope.LOCAL,
        aexpand: Callable[
            [
                str,
            ],
            Awaitable[Any],
        ] = None,
        ashrink: Callable[
            [
                any,
            ],
            Awaitable[str],
        ] = None,
        acollect: Callable[
            [
                str,
            ],
            Awaitable[Any],
        ] = None,
        predicate: Callable[[Any], bool] = None,
        convert_default: Callable[[Any], str] = None,
        default_widget: Optional[WidgetInput] = None,
        default_returnwidget: Optional[ReturnWidgetInput] = None,
    ):
        if inspect.isclass(cls):
            if issubclass(cls, Enum):
                identifier = "cls/" + cls.__name__.lower()
                shrink, expand = build_enum_shrink_expand(cls)
                ashrink = ashrink or shrink
                aexpand = aexpand or expand
                scope = Scope.GLOBAL

                def convert_default(x):
                    return x._name_

                default_widget = default_widget or WidgetInput(
                    kind="ChoiceWidget",
                    choices=[
                        ChoiceInput(label=key, value=key)
                        for key, value in cls.__members__.items()
                    ],
                )
                default_returnwidget = default_returnwidget or ReturnWidgetInput(
                    kind="ChoiceReturnWidget",
                    choices=[
                        ChoiceInput(label=key, value=key)
                        for key, value in cls.__members__.items()
                    ],
                )

        if convert_default is None:
            if hasattr(cls, "convert_default"):
                convert_default = cls.convert_default

        if aexpand is None:
            if not hasattr(cls, "aexpand") and scope == Scope.GLOBAL:
                raise StructureDefinitionError(
                    f"You need to pass 'expand' method or {cls} needs to implement a"
                    " aexpand method if it wants to become a GLOBAL structure"
                )
            aexpand = getattr(cls, "aexpand", shelve_aexpand)

        if ashrink is None:
            if not hasattr(cls, "ashrink") and scope == Scope.GLOBAL:
                raise StructureDefinitionError(
                    f"You need to pass 'ashrink' method or {cls} needs to implement a"
                    " ashrink method if it wants to become a GLOBAL structure"
                )
            ashrink = getattr(cls, "ashrink", shelve_ashrink)

        if acollect is None:
            if scope == Scope.GLOBAL:
                acollect = void_acollect
            else:
                acollect = getattr(cls, "acollect", shelve_acollect)

        if predicate is None:
            predicate = build_instance_predicate(cls)

        if identifier is None:
            if not hasattr(cls, "get_identifier"):
                raise StructureDefinitionError(
                    f"You need to pass 'identifier' or  {cls} needs to implement a"
                    " get_identifier method"
                )
            identifier = cls.get_identifier()

        if identifier in self.identifier_structure_map and not self.allow_overwrites:
            raise StructureOverwriteError(
                f"{identifier} is already registered. Previously registered"
                f" {self.identifier_structure_map[identifier]}"
            )

        self._identifier_expander_map[identifier] = aexpand
        self._identifier_collecter_map[identifier] = acollect
        self._identifier_shrinker_map[identifier] = ashrink
        self._identifier_predicate_map[identifier] = predicate

        self.identifier_structure_map[identifier] = cls
        self.identifier_scope_map[identifier] = scope
        self._structure_identifier_map[cls] = identifier
        self._structure_default_widget_map[cls] = default_widget
        self._structure_default_returnwidget_map[cls] = default_returnwidget
        self._structure_convert_default_map[cls] = convert_default

    def get_converter_for_annotation(self, annotation):
        try:
            return self._structure_annotation_map[annotation]
        except KeyError as e:
            raise StructureRegistryError(f"{annotation} is not registered") from e

    def register_annotation_converter(
        self,
        annotation: T,
        converter: Callable[[Type[T]], AnnotationInput],
        overwrite=False,
    ):
        if annotation in self._structure_annotation_map and not overwrite:
            raise StructureRegistryError(
                f"{annotation} is already registered: Specify overwrite=True to"
                " overwrite"
            )

        self._structure_annotation_map[annotation] = converter

    async def __aenter__(self):
        current_structure_registry.set(self)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        current_structure_registry.set(None)

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True


DEFAULT_STRUCTURE_REGISTRY = None


def get_current_structure_registry(allow_default=True):
    return current_structure_registry.get()
