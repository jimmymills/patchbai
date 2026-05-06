from mod_tui.layout.spec import LayoutSpec


def dashboard_layout() -> LayoutSpec:
    """The built-in landing layout used when no <cwd>/.mod_tui/layout.json exists."""
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "size": "60%", "widget": "OrchestratorChat"},
                {
                    "type": "vertical",
                    "size": "40%",
                    "children": [
                        {"id": "agents", "size": "50%", "widget": "AgentTable"},
                        {"id": "feed", "size": "50%", "widget": "ActivityFeed"},
                    ],
                },
            ],
        },
        "focus": "orch",
    })
