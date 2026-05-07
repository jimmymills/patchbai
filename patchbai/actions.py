from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ActionSpec:
    name: str
    callable: Callable
    description: str
    args_schema: dict


class ActionRegistry:
    """Enumerable action registry — name → ActionSpec."""

    def __init__(self) -> None:
        self._actions: dict[str, ActionSpec] = {}

    def register(self, name: str, fn: Callable, *, description: str, args_schema: dict) -> None:
        self._actions[name] = ActionSpec(
            name=name, callable=fn, description=description, args_schema=args_schema,
        )

    def get(self, name: str) -> ActionSpec:
        if name not in self._actions:
            raise KeyError(f"unknown action: {name}")
        return self._actions[name]

    def list(self) -> list[ActionSpec]:
        return sorted(self._actions.values(), key=lambda s: s.name)

    def invoke(self, name: str, args: dict) -> Any:
        spec = self.get(name)
        return spec.callable(**(args or {}))
