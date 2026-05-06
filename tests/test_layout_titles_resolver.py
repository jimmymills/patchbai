from mod_tui.layout.spec import Panel
from mod_tui.layout.titles import resolve_title
from mod_tui.widgets.agent_table import AgentTable
from mod_tui.widgets.diff_viewer import DiffViewer
from mod_tui.widgets.orchestrator_chat import OrchestratorChat
from mod_tui.widgets.placeholders import ActivityFeed


class _Bare:
    """Plain class — no DEFAULT_BORDER_TITLE, no default_border_title method."""


def test_explicit_panel_title_wins_over_widget_default():
    p = Panel(id="x", widget="OrchestratorChat", title="Custom")
    assert resolve_title(p, OrchestratorChat) == "Custom"


def test_static_default_border_title_used_when_panel_title_missing():
    assert resolve_title(Panel(id="orch", widget="OrchestratorChat"), OrchestratorChat) == "Orchestrator"
    assert resolve_title(Panel(id="agents", widget="AgentTable"), AgentTable) == "Agents"
    assert resolve_title(Panel(id="feed", widget="ActivityFeed"), ActivityFeed) == "Activity"
    assert resolve_title(Panel(id="diff", widget="DiffViewer"), DiffViewer) == "Diff"


def test_class_name_is_last_resort_fallback():
    assert resolve_title(Panel(id="x", widget="Bare"), _Bare) == "_Bare"


def test_resolver_swallows_classmethod_exceptions():
    class Boom:
        @classmethod
        def default_border_title(cls, props):
            raise RuntimeError("nope")
    assert resolve_title(Panel(id="x", widget="Boom"), Boom) == "Boom"
