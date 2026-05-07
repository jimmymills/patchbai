from patchbai.layout.spec import LayoutSpec


def dashboard_layout() -> LayoutSpec:
    """The built-in landing layout used when no <cwd>/.patchbai/layout.json exists.

    Mirrors the saved 'focused-agents' layout: orchestrator at 60%, with the
    agent table and activity feed sharing a tabs container at 40% (Agents
    active by default) so the activity feed doesn't compete for vertical
    space when the agent table is what the user usually wants to see.
    """
    return LayoutSpec.model_validate({
        "version": 1,
        "layout": {
            "type": "horizontal",
            "children": [
                {"id": "orch", "size": "60%", "widget": "OrchestratorChat",
                 "title": "Orchestrator"},
                {
                    "type": "tabs",
                    "size": "40%",
                    "children": [
                        {"id": "agents", "widget": "AgentTable",
                         "title": "Agents"},
                        {"id": "feed", "widget": "ActivityFeed",
                         "title": "Activity"},
                    ],
                    "active": "agents",
                },
            ],
        },
        "focus": "orch",
    })
