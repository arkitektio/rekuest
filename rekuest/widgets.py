from rekuest.api.schema import WidgetInput, ReturnWidgetInput


def SliderWidget(min=0, max=0):
    return WidgetInput(kind="SliderWidget", min=min, max=max)


def SearchWidget(query=""):
    return WidgetInput(kind="SearchWidget", query=query)


def ImageReturnWidget(query=""):
    return ReturnWidgetInput(kind="ImageReturnWidget", query=query)


def StringWidget(query=""):
    return WidgetInput(kind="StringWidget")
