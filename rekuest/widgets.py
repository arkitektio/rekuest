from rekuest.api.schema import WidgetInput, ReturnWidgetInput, ChoiceInput
from rekuest.scalars import SearchQuery
from typing import List


def SliderWidget(min: int = None, max: int = None, **kwargs) -> WidgetInput:
    """Generate a slider widget.

    Args:
        min (int, optional): The mininum value. Defaults to None.
        max (int, optional): The maximum value. Defaults to None.

    Returns:
        WidgetInput: _description_
    """
    return WidgetInput(kind="SliderWidget", min=min, max=max, **kwargs)


def SearchWidget(query: SearchQuery, ward: str, **kwargs) -> WidgetInput:
    """Generte a search widget.

    A search widget is a widget that allows the user to search for a specifc
    structure utilizing a GraphQL query and running it on a ward (a frontend 
    registered helper that can run the query). The query needs to follow
    the SearchQuery type.

    Args:
        query (SearchQuery): The serach query as a search query object or string
        ward (str): The ward key

    Returns:
        WidgetInput: _description_
    """ """P"""
    return WidgetInput(kind="SearchWidget", query=query, ward=ward, **kwargs)


def ChoiceWidget(choices: List[ChoiceInput], **kwargs) -> WidgetInput:
    """Generate a choice widget.

    A choice widget is a widget that renderes a list of choices with the
    value of the choice being highlighted.

    Args:
        choices (List[ChoiceInput]): The choices

    Returns:
        WidgetInput: _description_
    """
    return WidgetInput(kind="ChoiceWidget", choices=choices, **kwargs)


def StringWidget(as_paragraph: bool = False, **kwargs) -> WidgetInput:
    """Generate a string widget.

    Args:
        as_paragraph (bool, optional): Should we render the string as a paragraph.Defaults to False.

    Returns:
        WidgetInput: _description_
    """
    return WidgetInput(kind="StringWidget", asParagraph=as_paragraph, **kwargs)


def ParagraphWidget(**kwargs) -> WidgetInput:
    """Generate a string widget.

    Args:
        as_paragraph (bool, optional): Should we render the string as a paragraph.Defaults to False.

    Returns:
        WidgetInput: _description_
    """
    return WidgetInput(kind="StringWidget", asParagraph=True, **kwargs)


def CustomWidget(hook: str, **kwargs) -> WidgetInput:
    """Generate a custom widget.

    A custom widget is a widget that is rendered by a frontend registered hook
    that is passed the input value.

    Args:
        hook (str): The hook key

    Returns:
        WidgetInput: _description_
    """
    return WidgetInput(kind="CustomWidget", hook=hook, **kwargs)


def CustomReturnWidget(hook: str, **kwargs) -> ReturnWidgetInput:
    """A custom return widget.

    A custom return widget is a widget that is rendered by a frontend registered hook
    that is passed the input value.

    Args:
        hook (str): The hool

    Returns:
        ReturnWidgetInput: _description_
    """ """"""
    return ReturnWidgetInput(kind="CustomReturnWidget", hook=hook, **kwargs)


def ChoiceReturnWidget(choices: List[ChoiceInput], **kwargs) -> ReturnWidgetInput:
    """A choice return widget.

    A choice return widget is a widget that renderes a list of choices with the
    value of the choice being highlighted.

    Args:
        choices (List[ChoiceInput]): The choices

    Returns:
        ReturnWidgetInput: _description_
    """
    return ReturnWidgetInput(kind="ChoiceReturnWidget", choices=choices, **kwargs)
