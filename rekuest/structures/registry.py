import contextvars
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional, Type, TypeVar

from rekuest.api.schema import (
    ChoiceInput,
    ReturnWidgetInput,
    WidgetInput,
    AnnotationInput,
    Scope,
)
from pydantic import BaseModel, Field

from .errors import (
    StructureDefinitionError,
    StructureOverwriteError,
    StructureRegistryError,
)

current_structure_registry = contextvars.ContextVar("current_structure_registry")


async def id_shrink(self):
    return self.id


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
    allow_overwrites: bool = True
    allow_auto_register: bool = True

    identifier_structure_map: Dict[str, Type] = Field(
        default_factory=dict, exclude=True
    )
    identifier_scope_map: Dict[str, Scope] = Field(default_factory=dict, exclude=True)
    _identifier_expander_map: Dict[str, Callable[[str], Awaitable[Any]]] = {}
    _identifier_shrinker_map: Dict[str, Callable[[Any], Awaitable[str]]] = {}

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
            raise StructureRegistryError(f"{key} is not registered") from e

    def get_shrinker_for_identifier(self, key):
        try:
            return self._identifier_shrinker_map[key]
        except KeyError as e:
            raise StructureRegistryError(f"{key} is not registered") from e

    def register_expander(self, key, expander):
        self._identifier_expander_map[key] = expander

    def get_widget_input(self, cls) -> Optional[WidgetInput]:
        return self._structure_default_widget_map.get(cls, None)

    def get_returnwidget_input(self, cls) -> Optional[ReturnWidgetInput]:
        return self._structure_default_returnwidget_map.get(cls, None)

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
        expand: Callable[
            [
                str,
            ],
            Awaitable[Any],
        ] = None,
        shrink: Callable[
            [
                any,
            ],
            Awaitable[str],
        ] = None,
        convert_default: Callable[[Any], str] = None,
        default_widget: WidgetInput = None,
        default_returnwidget: ReturnWidgetInput = None,
    ):
        if issubclass(cls, Enum):
            identifier = "cls/" + cls.__name__.lower()
            shrink, expand = build_enum_shrink_expand(cls)
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

        if expand is None:
            if not hasattr(cls, "aexpand"):
                raise StructureDefinitionError(
                    f"You need to pass 'aexpand' method or {cls} needs to implement a"
                    " aexpand method"
                )
            expand = cls.aexpand

        if shrink is None:
            if not hasattr(cls, "ashrink"):
                if issubclass(cls, BaseModel):
                    if "id" in cls.__fields__:
                        shrink = id_shrink
                    else:
                        raise StructureDefinitionError(
                            f"You need to pass 'ashrink' method or {cls} needs to"
                            " implement a ashrink method. A BaseModel can be"
                            " automatically shrinked by providing an id field"
                        )
                else:
                    raise StructureDefinitionError(
                        f"You need to pass 'ashrink' method or {cls} needs to implement"
                        " a ashrink method"
                    )
            else:
                shrink = cls.ashrink

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

        self._identifier_expander_map[identifier] = expand
        self._identifier_shrinker_map[identifier] = shrink
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


def register_structure(
    identifier: str = None,
    expand: Callable[
        [
            str,
        ],
        Awaitable[Any],
    ] = None,
    shrink: Callable[
        [
            any,
        ],
        Awaitable[str],
    ] = None,
    default_widget: WidgetInput = None,
    registry: StructureRegistry = None,
):
    """A Decorator for registering a structure

    Args:
        identifier ([type], optional): [description]. Defaults to None.
        expand ([type], optional): [description]. Defaults to None.
        shrink ([type], optional): [description]. Defaults to None.
        default_widget ([type], optional): [description]. Defaults to None.
    """

    registry = registry or get_current_structure_registry()

    def func(cls):
        registry.register_as_structure(cls, identifier, expand, shrink, default_widget)
        return cls

    return func
