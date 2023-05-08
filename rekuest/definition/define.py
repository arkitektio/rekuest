from enum import Enum
from typing import Callable, List, Tuple, Type
from .utils import get_type_hints, is_annotated
import inflection
from rekuest.api.schema import (
    PortInput,
    ChildPortInput,
    DefinitionInput,
    NodeKindInput,
    PortKindInput,
    AnnotationInput,
    Scope,
    WidgetInput,
    ReturnWidgetInput,
    PortGroupInput,
)
import inspect
from docstring_parser import parse
from rekuest.definition.errors import DefinitionError

from rekuest.structures.registry import (
    StructureRegistry,
)
from typing import Protocol, runtime_checkable, Optional, List, Any, Dict


def convert_child_to_childport(
    cls: Type,
    registry: StructureRegistry,
    nullable: bool = False,
    annotations: List[AnnotationInput] = None,
) -> Tuple[ChildPortInput, Callable]:
    """Converts a element of a annotation to a child port

    Args:
        cls (Type): The type (class or annotation) of the elemtn
        registry (StructureRegistry): The structure registry to use
        nullable (bool, optional): Is this type optional (recursive parameter).
            Defaults to False.
        is_return (bool, optional): Is this a return type?. Defaults to False.
        annotations (List[AnnotationInput], optional): The annotations for this element.
            Defaults to None.

    Returns:
        Tuple[ChildPortInput, WidgetInput, Callable]: The child port, the widget and the
         converter for the default
    """

    if is_annotated(cls):
        real_type = cls.__args__[0]

        annotations = [
            registry.get_converter_for_annotation(i.__class__)(i)
            for i in cls.__metadata__
        ]

        return convert_child_to_childport(
            real_type,
            registry,
            nullable=nullable,
            annotations=annotations,
        )

    if cls.__module__ == "typing":
        if hasattr(cls, "_name"):
            # We are dealing with a Typing Var?
            if cls._name == "List":
                child, nested_converter = convert_child_to_childport(
                    cls.__args__[0], registry, nullable=False
                )

                return (
                    ChildPortInput(
                        kind=PortKindInput.LIST,
                        child=child,
                        scope=Scope.GLOBAL,
                        nullable=nullable,
                        annotations=annotations,
                    ),
                    lambda default: (
                        [nested_converter(ndefault) for ndefault in default]
                        if default
                        else None
                    ),
                )

            if cls._name == "Dict":
                child, nested_converter = convert_child_to_childport(
                    cls.__args__[1], "omit", registry, nullable=False
                )
                return (
                    ChildPortInput(
                        kind=PortKindInput.DICT,
                        child=child,
                        scope=Scope.GLOBAL,
                        nullable=nullable,
                        annotations=annotations,
                    ),
                    lambda default: (
                        {
                            key: item in nested_converter(item)
                            for key, item in default.items()
                        }
                        if default
                        else None
                    ),
                )

        if hasattr(cls, "__args__"):
            if cls.__args__[1] == type(None):
                return convert_child_to_childport(
                    cls.__args__[0], registry, nullable=True
                )

    if inspect.isclass(cls):
        # Generic Cases

        if not issubclass(cls, Enum) and issubclass(cls, bool):
            t = ChildPortInput(
                kind=PortKindInput.BOOL,
                nullable=nullable,
                scope=Scope.GLOBAL,
                annotations=annotations,
            )  # catch bool is subclass of int
            return t, str

        if not issubclass(cls, Enum) and issubclass(cls, int):
            return (
                ChildPortInput(
                    kind=PortKindInput.INT,
                    nullable=nullable,
                    scope=Scope.GLOBAL,
                    annotations=annotations,
                ),
                int,
            )
        if not issubclass(cls, Enum) and issubclass(cls, str):
            return (
                ChildPortInput(
                    kind=PortKindInput.STRING,
                    nullable=nullable,
                    scope=Scope.GLOBAL,
                    annotations=annotations,
                ),
                str,
            )

    identifier = registry.get_identifier_for_structure(cls)
    scope = registry.get_scope_for_identifier(identifier)
    default_converter = registry.get_default_converter_for_structure(cls)
    assign_widget = registry.get_widget_input(cls)
    return_widget = registry.get_returnwidget_input(cls)

    return (
        ChildPortInput(
            kind=PortKindInput.STRUCTURE,
            identifier=identifier,
            scope=scope,
            nullable=nullable,
            annotations=annotations,
            assignWidget=assign_widget,
            returnWidget=return_widget,
        ),
        default_converter,
    )


def convert_object_to_port(
    cls,
    key,
    registry: StructureRegistry,
    widget=None,
    return_widget=None,
    default=None,
    description=None,
    nullable=False,
    groups: Optional[List[str]] = None,
    annotations=[],
) -> PortInput:
    """
    Convert a class to an Port
    """
    if is_annotated(cls):
        real_type = cls.__args__[0]

        annotations = [
            registry.get_converter_for_annotation(i.__class__)(i)
            for i in cls.__metadata__
        ]

        return convert_object_to_port(
            real_type,
            key,
            registry,
            widget=widget,
            default=default,
            nullable=nullable,
            annotations=annotations,
            groups=groups,
        )

    if cls.__module__ == "typing":
        if hasattr(cls, "_name"):
            # We are dealing with a Typing Var?
            if cls._name == "List":
                child, converter = convert_child_to_childport(
                    cls.__args__[0], registry, nullable=False
                )
                return PortInput(
                    kind=PortKindInput.LIST,
                    assignWidget=widget,
                    returnWidget=return_widget,
                    scope=Scope.GLOBAL,
                    key=key,
                    child=child,
                    default=[converter(item) for item in default] if default else None,
                    nullable=nullable,
                    annotations=annotations,
                    description=description,
                    groups=groups,
                )

            if cls._name == "Dict":
                child, converter = convert_child_to_childport(
                    cls.__args__[1], registry, nullable=False
                )
                return PortInput(
                    kind=PortKindInput.DICT,
                    assignWidget=widget,
                    scope=Scope.GLOBAL,
                    returnWidget=return_widget,
                    key=key,
                    child=child,
                    default=(
                        {key: converter(item) for key, item in default.items()}
                        if default
                        else None
                    ),
                    nullable=nullable,
                    annotations=annotations,
                    description=description,
                    groups=groups,
                )

            if cls._name == "Union":
                raise NotImplementedError("Union is not supported yet")

        if hasattr(cls, "__args__"):
            if cls.__args__[1] == type(None):
                return convert_object_to_port(
                    cls.__args__[0],
                    key,
                    registry,
                    default=default,
                    nullable=True,
                    widget=widget,
                    return_widget=return_widget,
                    annotations=annotations,
                    description=description,
                    groups=groups,
                )

    if inspect.isclass(cls):
        # Generic Cases

        if (
            not issubclass(cls, Enum)
            and issubclass(cls, bool)
            or (default is not None and isinstance(default, bool))
        ):
            t = PortInput(
                kind=PortKindInput.BOOL,
                scope=Scope.GLOBAL,
                assignWidget=widget,
                returnWidget=return_widget,
                key=key,
                default=default,
                nullable=nullable,
                annotations=annotations,
                description=description,
                groups=groups,
            )  # catch bool is subclass of int
            return t

        if (
            not issubclass(cls, Enum)
            and issubclass(cls, int)
            or (default is not None and isinstance(default, int))
        ):
            return PortInput(
                kind=PortKindInput.INT,
                assignWidget=widget,
                scope=Scope.GLOBAL,
                returnWidget=return_widget,
                key=key,
                default=default,
                nullable=nullable,
                annotations=annotations,
                description=description,
                groups=groups,
            )

        if (
            not issubclass(cls, Enum)
            and issubclass(cls, float)
            or (default is not None and isinstance(default, float))
        ):
            return PortInput(
                kind=PortKindInput.FLOAT,
                assignWidget=widget,
                returnWidget=return_widget,
                scope=Scope.GLOBAL,
                key=key,
                default=default,
                nullable=nullable,
                annotations=annotations,
                description=description,
                groups=groups,
            )

        if (
            not issubclass(cls, Enum)
            and issubclass(cls, str)
            or (default is not None and isinstance(default, str))
        ):
            return PortInput(
                kind=PortKindInput.STRING,
                assignWidget=widget,
                returnWidget=return_widget,
                scope=Scope.GLOBAL,
                key=key,
                default=default,
                nullable=nullable,
                annotations=annotations,
                description=description,
                groups=groups,
            )

    identifier = registry.get_identifier_for_structure(cls)
    scope = registry.get_scope_for_identifier(identifier)
    default_converter = registry.get_default_converter_for_structure(cls)
    widget = widget or registry.get_widget_input(cls)
    return_widget = return_widget or registry.get_returnwidget_input(cls)

    return PortInput(
        kind=PortKindInput.STRUCTURE,
        identifier=identifier,
        assignWidget=widget,
        scope=scope,
        returnWidget=return_widget,
        key=key,
        default=default_converter(default) if default else None,
        nullable=nullable,
        annotations=annotations,
        description=description,
        groups=groups,
    )


GroupMap = Dict[str, List[str]]
WidgetMap = Dict[str, List[WidgetInput]]
ReturnWidgetMap = Dict[str, List[ReturnWidgetInput]]


def prepare_definition(
    function: Callable,
    structure_registry: StructureRegistry,
    interface: str = None,
    widgets: Optional[WidgetMap] = None,
    return_widgets: Optional[ReturnWidgetMap] = None,
    groups: Optional[GroupMap] = None,
    port_groups: List[PortGroupInput] = None,
    allow_empty_doc=False,
    interfaces: Optional[List[str]] = None,
    omitfirst=None,
    omitlast=None,
    omitkeys=[],
    allow_annotations: bool = True,
) -> DefinitionInput:
    """Define

    Define a callable (async function, sync function, async generator, async
    generator) in the context of arkitekt and
    return its definition(input).

    Attention this definition is not yet registered in the
    arkitekt registry. This is done by the create_template function ( which will
    validate he definition with your local arkitekt instance
    and raise an error if the definition is not compatible with your arkitekt version)


    Args:
        function (): The function you want to define
    """

    assert structure_registry is not None, "You need to pass a StructureRegistry"

    is_generator = inspect.isasyncgenfunction(function) or inspect.isgeneratorfunction(
        function
    )

    sig = inspect.signature(function)
    widgets = widgets or {}

    port_groups = port_groups or []
    port_groups_name = [i.key for i in port_groups]
    groups = groups or {}
    for key, grouplist in groups.items():
        for group in grouplist:
            if group not in port_groups_name:
                raise DefinitionError(
                    f"Error mapping {group} to a group in port groups for port {key}:  Please define a PortGroup for {group}"
                )

    return_widgets = return_widgets or {}
    interfaces = interfaces or []
    # Generate Args and Kwargs from the Annotation
    args: List[PortInput] = []
    returns: List[PortInput] = []

    # Docstring Parser to help with descriptions
    docstring = parse(function.__doc__)
    if docstring.long_description is None:
        assert allow_empty_doc is not False, (
            f"We don't allow empty documentation for function {function.__name__}."
            " Please Provide"
        )

    type_hints = get_type_hints(function, include_extras=allow_annotations)
    function_ins_annotation = sig.parameters

    doc_param_map = {param.arg_name: param.description for param in docstring.params}

    # TODO: Update with documentatoin.... (Set description for portexample)

    doc_returns_map = {
        f"return{index}": param.description
        for index, param in enumerate(docstring.many_returns)
    }

    for index, (key, value) in enumerate(function_ins_annotation.items()):
        # We can skip arguments if the builder is going to provide additional arguments
        if omitfirst is not None and index < omitfirst:
            continue
        if omitlast is not None and index > omitlast:
            continue
        if key in omitkeys:
            continue

        widget = widgets.get(key, None)
        return_widget = return_widgets.get(key, None)
        default = value.default if value.default != inspect.Parameter.empty else None
        cls = type_hints.get(key, type(default) if default is not None else None)
        this_port_groups = groups.get(key, None)

        if cls is None:
            raise DefinitionError(
                f"Could not find type hint for {key} in {function.__name__}. Please provide a type hint (or default) for this argument."
            )

        try:
            args.append(
                convert_object_to_port(
                    cls,
                    key,
                    structure_registry,
                    widget=widget,
                    return_widget=return_widget,
                    default=default,
                    description=doc_param_map.get(key, None),
                    groups=this_port_groups,
                )
            )
        except Exception as e:
            raise DefinitionError(
                f"Could not convert Argument of function {function.__name__} to"
                f" ArgPort: {value}"
            ) from e

    function_outs_annotation = sig.return_annotation

    if hasattr(function_outs_annotation, "_name"):
        if function_outs_annotation._name == "Tuple":
            try:
                for index, cls in enumerate(function_outs_annotation.__args__):
                    return_widget = return_widgets.get(f"return{index}", None)
                    widget = widgets.get(f"return{index}", None)
                    this_port_groups = groups.get(f"return{index}", None)
                    returns.append(
                        convert_object_to_port(
                            cls,
                            f"return{index}",
                            structure_registry,
                            description=doc_returns_map.get(f"return{index}", None),
                            return_widget=return_widget,
                            widget=widget,
                            groups=this_port_groups,
                        )
                    )
            except Exception as e:
                raise DefinitionError(
                    f"Could not convert Return of function {function.__name__} to"
                    f" ArgPort: {cls}"
                ) from e
        else:
            try:
                return_widget = return_widgets.get("return0", None)
                widget = widgets.get("return0", None)
                this_port_groups = groups.get("return0", None)
                returns.append(
                    convert_object_to_port(
                        function_outs_annotation,
                        "return0",
                        structure_registry,
                        return_widget=return_widget,
                        widget=widget,
                        groups=this_port_groups,
                    )
                )  # Other types will be converted to normal lists and shit
            except Exception as e:
                raise DefinitionError(
                    f"Could not convert Return of function {function.__name__} to"
                    f" ArgPort: {function_outs_annotation}"
                ) from e
    else:
        # We are dealing with a non tuple return
        if function_outs_annotation is None:
            pass

        elif function_outs_annotation.__name__ != "_empty":  # Is it not empty
            return_widget = return_widgets.get("return0", None)
            widget = widgets.get("return0", None)
            this_port_groups = groups.get("return0", None)
            returns.append(
                convert_object_to_port(
                    function_outs_annotation,
                    "return0",
                    structure_registry,
                    widget=widget,
                    return_widget=return_widget,
                    groups=this_port_groups,
                )
            )

    # Documentation Parsing

    name = docstring.short_description or function.__name__
    interface = interface or inflection.underscore(
        function.__name__
    )  # convert this to camelcase
    description = docstring.long_description or "No Description"

    x = DefinitionInput(
        **{
            "name": name,
            "interface": interface,
            "description": description,
            "args": args,
            "returns": returns,
            "kind": NodeKindInput.GENERATOR if is_generator else NodeKindInput.FUNCTION,
            "interfaces": interfaces,
            "portGroups": port_groups,
        }
    )

    return x
