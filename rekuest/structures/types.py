from typing import Protocol, Optional
from rekuest.api.schema import WidgetInput, ReturnWidgetInput, PortInput


class PortBuilder(Protocol):
    def __call__(
        self,
        cls: type,
        assign_widget: Optional[WidgetInput],
        return_widget: Optional[ReturnWidgetInput],
    ) -> PortInput:
        ...
