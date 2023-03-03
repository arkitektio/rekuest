from rekuest.api.schema import WidgetInput, ReturnWidgetInput, ChoiceInput
from typing import List

def SliderWidget(min=None, max=None):
    return WidgetInput(kind="SliderWidget", min=min, max=max)


def SearchWidget(query, ward):
    return WidgetInput(kind="SearchWidget", query=query, ward=ward)


def ImageReturnWidget(query, ward):
    return ReturnWidgetInput(kind="ImageReturnWidget", query=query, ward=ward)


def StringWidget(as_paragraph=False):
    return WidgetInput(kind="StringWidget", asParagraph=as_paragraph)


def CustomWidget(hook: str):
    return WidgetInput(kind="CustomWidget", hook=hook)


def CustomReturnWidget(hook: str, ward: str):
    return ReturnWidgetInput(kind="CustomReturnWidget", hook=hook, ward=ward)

def ChoiceReturnWidget(choices: List[ChoiceInput]):
    return ReturnWidgetInput(kind="ChoiceReturnWidget", choices=choices)