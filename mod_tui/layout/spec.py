from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Panel(BaseModel):
    """A leaf node — one widget instance."""
    model_config = ConfigDict(extra="forbid")

    id: str
    widget: str
    props: dict = Field(default_factory=dict)
    size: str | None = None
    title: str | None = None


class Container(BaseModel):
    """A non-leaf node — splits its area horizontally or vertically."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["horizontal", "vertical"]
    size: str | None = None
    children: list["Node"] = Field(min_length=1)


class Tabs(BaseModel):
    """A tabbed leaf-container — each child is one widget reachable via a
    per-panel tab strip. Each tab holds exactly one Panel; splits inside
    a single tab are not allowed.

    `active` is the panel id of the initial tab; when None, the first
    child is the initial tab."""
    model_config = ConfigDict(extra="forbid")

    type: Literal["tabs"]
    size: str | None = None
    children: list[Panel] = Field(min_length=1)
    active: str | None = None

    @model_validator(mode="after")
    def _active_must_match_a_child(self) -> "Tabs":
        if self.active is not None:
            ids = {p.id for p in self.children}
            if self.active not in ids:
                raise ValueError(
                    f"Tabs.active={self.active!r} does not match any child panel id; "
                    f"expected one of {sorted(ids)}"
                )
        return self


# Discriminated union: Container and Tabs share the `children` shape but
# differ on `type`. Pydantic v2 dispatches on the literal `type` value.
# Panel has no `type` field and is matched by absence — placed last so
# union resolution prefers the typed branches first.
_TypedNode = Annotated[Union[Container, Tabs], Field(discriminator="type")]
Node = Union[_TypedNode, Panel]
Container.model_rebuild()


class CustomWidget(BaseModel):
    """A user/orchestrator-supplied Textual widget class."""
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str


class LayoutSpec(BaseModel):
    """Root of the layout description.

    Validation invariant: at most one panel with widget='OrchestratorChat'
    in `layout`. The "at least one chat across all tabs" half is enforced
    by the Workspace model — a single LayoutSpec may have zero chats
    (e.g., a logs-only tab).

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
    def _at_most_one_orchestrator_chat(self) -> "LayoutSpec":
        count = _count_orchestrator(self.layout)
        if count > 1:
            raise ValueError(
                "LayoutSpec must contain at most one OrchestratorChat panel"
            )
        return self


def _count_orchestrator(node: Node) -> int:
    if isinstance(node, Panel):
        return 1 if node.widget == "OrchestratorChat" else 0
    if isinstance(node, Tabs):
        return sum(1 for c in node.children if c.widget == "OrchestratorChat")
    # Container
    return sum(_count_orchestrator(c) for c in node.children)
