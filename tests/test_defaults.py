from patchfeld.layout.defaults import dashboard_layout
from patchfeld.layout.spec import Container, Panel, Tabs


def test_dashboard_validates():
    dashboard_layout()  # raises if invalid


def test_dashboard_has_three_panels_in_correct_arrangement():
    spec = dashboard_layout()
    root = spec.layout
    assert isinstance(root, Container) and root.type == "horizontal"
    assert len(root.children) == 2

    left = root.children[0]
    assert isinstance(left, Panel) and left.widget == "OrchestratorChat"
    assert left.id == "orch"

    right = root.children[1]
    assert isinstance(right, Tabs)
    assert len(right.children) == 2
    a, b = right.children
    assert isinstance(a, Panel) and a.widget == "AgentTable" and a.id == "agents"
    assert isinstance(b, Panel) and b.widget == "ActivityFeed" and b.id == "feed"
    # Agents is the active tab so the user lands on the agent table, not the
    # activity feed, when seeding a fresh workspace.
    assert right.active == "agents"


def test_dashboard_focus_is_orchestrator():
    assert dashboard_layout().focus == "orch"
