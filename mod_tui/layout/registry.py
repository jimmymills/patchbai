from textual.widget import Widget


class UnknownWidgetError(KeyError):
    """Raised when a LayoutSpec references a widget name that is not registered."""


class WidgetRegistry:
    """Maps widget-type strings (as used in LayoutSpec) to Textual classes.

    Mode-C `register_custom_widget(name, source)` (which `exec`s code into an
    isolated namespace) is intentionally NOT implemented in this plan — it
    arrives in a later plan. This registry only supports curated registration
    of already-imported classes.
    """

    def __init__(self) -> None:
        self._classes: dict[str, type[Widget]] = {}

    def register(self, name: str, cls: type[Widget]) -> None:
        self._classes[name] = cls

    def get(self, name: str) -> type[Widget]:
        try:
            return self._classes[name]
        except KeyError as e:
            raise UnknownWidgetError(name) from e

    def known(self) -> list[str]:
        return list(self._classes.keys())
