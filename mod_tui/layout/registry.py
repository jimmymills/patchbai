from dataclasses import dataclass, field

from textual.widget import Widget


class UnknownWidgetError(KeyError):
    """Raised when a LayoutSpec references a widget name that is not registered."""


@dataclass(frozen=True)
class WidgetInfo:
    name: str
    cls: type[Widget]
    description: str = ""
    props_schema: dict = field(default_factory=dict)


class WidgetRegistry:
    """Maps widget-type strings (as used in LayoutSpec) to Textual classes,
    plus optional metadata for the orchestrator's list_widgets tool.

    Mode-C `register_custom_widget(name, source)` (which `exec`s code into an
    isolated namespace) is intentionally NOT implemented in this plan — it
    arrives in plan 6.
    """

    def __init__(self) -> None:
        self._infos: dict[str, WidgetInfo] = {}

    def register(
        self,
        name: str,
        cls: type[Widget],
        *,
        description: str = "",
        props_schema: dict | None = None,
    ) -> None:
        self._infos[name] = WidgetInfo(
            name=name, cls=cls,
            description=description,
            props_schema=dict(props_schema) if props_schema else {},
        )

    def get(self, name: str) -> type[Widget]:
        if name not in self._infos:
            raise UnknownWidgetError(name)
        return self._infos[name].cls

    def known(self) -> list[str]:
        return list(self._infos.keys())

    def describe(self, name: str) -> WidgetInfo:
        if name not in self._infos:
            raise KeyError(name)
        return self._infos[name]

    def describe_all(self) -> list[WidgetInfo]:
        return sorted(self._infos.values(), key=lambda i: i.name)
