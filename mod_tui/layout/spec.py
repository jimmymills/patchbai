from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Panel(BaseModel):
    """A leaf node — one widget instance."""
    model_config = ConfigDict(extra="forbid")

    id: str
    widget: str
    props: dict = Field(default_factory=dict)
    size: str | None = None


class Container(BaseModel):
    """A non-leaf node — splits its area horizontally or vertically."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["horizontal", "vertical"]
    size: str | None = None
    children: list["Node"] = Field(min_length=1)


# Node disambiguation is load-bearing on extra="forbid":
# Container has required {type, children}; Panel has required {id, widget}.
# Pydantic v2 smart-union tries Container first; a Panel dict fails Container
# validation because extra="forbid" rejects {id, widget} as unknown keys, then
# falls through to Panel. DO NOT relax extra="forbid" on either model without
# replacing this with a discriminated union.
Node = Union[Container, Panel]
Container.model_rebuild()


class CustomWidget(BaseModel):
    """A user/orchestrator-supplied Textual widget class. Mode C — wired in
    a later plan; the field exists now so the spec format is stable."""
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str


class LayoutSpec(BaseModel):
    """Root of the layout description.

    Validation invariant: exactly one panel with widget='OrchestratorChat'
    must exist anywhere in `layout`. Specs that violate this are rejected.

    The `focus` field names a panel id to receive keyboard focus on apply,
    but is NOT validated against the tree at parse time — LayoutEngine.apply
    silently no-ops if the id does not exist when the layout is mounted.
    """
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    layout: Node
    focus: str | None = None
    custom_widgets: list[CustomWidget] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one_orchestrator_chat(self) -> "LayoutSpec":
        count = _count_orchestrator(self.layout)
        if count == 0:
            raise ValueError(
                "LayoutSpec must contain a panel with widget='OrchestratorChat'"
            )
        if count > 1:
            raise ValueError(
                "LayoutSpec must contain exactly one OrchestratorChat panel"
            )
        return self


def _count_orchestrator(node: Node) -> int:
    if isinstance(node, Panel):
        return 1 if node.widget == "OrchestratorChat" else 0
    return sum(_count_orchestrator(c) for c in node.children)
