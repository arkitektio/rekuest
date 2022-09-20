from rekuest.api.schema import WidgetInput, ReturnWidgetInput


def SliderWidget(min=0, max=0):
    return WidgetInput(kind="SliderWidget", min=min, max=max)


def SearchWidget(query, ward):
    return WidgetInput(kind="SearchWidget", query=query, ward=ward)


def ImageReturnWidget(query=""):
    return ReturnWidgetInput(kind="ImageReturnWidget", query=query)


def StringWidget(as_paragraph=False):
    return WidgetInput(kind="StringWidget", as_paragraph=as_paragraph)


def CustomWidget(hook: str):
    return WidgetInput(kind="CustomWidget", hook=hook)


def CustomReturnWidget(hook: str):
    return ReturnWidgetInput(kind="CustomReturnWidget", hook=hook)
