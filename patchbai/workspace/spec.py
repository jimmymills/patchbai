from pydantic import BaseModel, ConfigDict, Field, model_validator

from patchbai.layout.spec import LayoutSpec, Node, Panel, Tabs


class Tab(BaseModel):
    """One app-level tab. Owns its own LayoutSpec, which is independently
    mutable. `id` is stable across the tab's lifetime and used by
    switch_tab/close_tab tool calls. `title` is the user-facing tab-strip
    label."""
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    layout: LayoutSpec


class Workspace(BaseModel):
    """Top-level container. Holds a list of Tabs and an active id.

    Invariants:
    - Non-empty tab list.
    - `active` references one of `tabs[].id`.
    - At least one OrchestratorChat panel exists across all tabs combined.
    - Tab ids are unique.
    """
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    tabs: list[Tab] = Field(min_length=1)
    active: str
    active_theme: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> "Workspace":
        ids = [t.id for t in self.tabs]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate tab id in {ids}")
        if self.active not in set(ids):
            raise ValueError(
                f"active tab id {self.active!r} not in tab ids {ids}"
            )
        if not any(_contains_chat(t.layout.layout) for t in self.tabs):
            raise ValueError(
                "Workspace must contain at least one OrchestratorChat panel "
                "across all tabs"
            )
        return self


def _contains_chat(node: Node) -> bool:
    if isinstance(node, Panel):
        return node.widget == "OrchestratorChat"
    if isinstance(node, Tabs):
        # Tabs.children is list[Panel] (leaf-only invariant in spec.py), so
        # a flat scan is sufficient — no recursion needed. If Tabs ever
        # accepts nested containers, this branch must recurse like Container.
        return any(c.widget == "OrchestratorChat" for c in node.children)
    # node is Container — exhausted by the discriminated Node union.
    return any(_contains_chat(c) for c in node.children)


def workspace_from_layout(spec: LayoutSpec, *, tab_id: str = "default",
                          title: str = "default") -> Workspace:
    """Build a single-tab Workspace wrapping a LayoutSpec — used by app
    launch to seed the workspace from the built-in dashboard or migrate
    a legacy layout.json."""
    return Workspace(
        version=1,
        tabs=[Tab(id=tab_id, title=title, layout=spec)],
        active=tab_id,
    )
