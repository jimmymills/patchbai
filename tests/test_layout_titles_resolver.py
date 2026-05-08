from patchfeld.layout.spec import Panel
from patchfeld.layout.titles import resolve_title
from patchfeld.widgets.agent_table import AgentTable
from patchfeld.widgets.diff_viewer import DiffViewer
from patchfeld.widgets.orchestrator_chat import OrchestratorChat
from patchfeld.widgets.activity_feed import ActivityFeed


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


from patchfeld.widgets.agent_transcript import AgentTranscript
from patchfeld.widgets.file_tree import FileTree
from patchfeld.widgets.file_viewer import FileViewer
from patchfeld.widgets.log_tail import LogTail
from patchfeld.widgets.markdown import Markdown
from patchfeld.widgets.notebook import Notebook
from patchfeld.widgets.rich_transcript import RichTranscript
from patchfeld.widgets.terminal import Terminal


def test_file_tree_default_title_uses_path():
    p = Panel(id="t", widget="FileTree", props={"path": "/Users/me/proj/src"})
    assert resolve_title(p, FileTree) == "Files: /Users/me/proj/src"


def test_file_tree_default_without_path_falls_through():
    p = Panel(id="t", widget="FileTree")
    assert resolve_title(p, FileTree) == "Files"


def test_file_viewer_default_uses_basename():
    p = Panel(id="v", widget="FileViewer", props={"file_path": "/a/b/c.py"})
    assert resolve_title(p, FileViewer) == "File: c.py"


def test_file_viewer_without_file_path_falls_through():
    p = Panel(id="v", widget="FileViewer")
    assert resolve_title(p, FileViewer) == "File"


def test_markdown_default_uses_basename():
    p = Panel(id="m", widget="Markdown", props={"file_path": "/a/b/README.md"})
    assert resolve_title(p, Markdown) == "Markdown: README.md"


def test_markdown_without_file_path_falls_through():
    p = Panel(id="m", widget="Markdown")
    assert resolve_title(p, Markdown) == "Markdown"


def test_log_tail_default_uses_basename():
    p = Panel(id="l", widget="LogTail", props={"file_path": "/var/log/app.log"})
    assert resolve_title(p, LogTail) == "Log: app.log"


def test_log_tail_without_file_path_falls_through():
    p = Panel(id="l", widget="LogTail")
    assert resolve_title(p, LogTail) == "Log"


def test_notebook_default_uses_name():
    p = Panel(id="n", widget="Notebook", props={"name": "ideas"})
    assert resolve_title(p, Notebook) == "Note: ideas"


def test_notebook_without_name_falls_through():
    p = Panel(id="n", widget="Notebook")
    assert resolve_title(p, Notebook) == "Note"


def test_terminal_default_uses_command_basename():
    p = Panel(id="t", widget="Terminal", props={"command": ["/usr/bin/zsh", "-l"]})
    assert resolve_title(p, Terminal) == "Terminal: zsh"


def test_terminal_without_command_falls_through():
    p = Panel(id="t", widget="Terminal")
    assert resolve_title(p, Terminal) == "Terminal"


def test_agent_transcript_default_includes_agent_id():
    p = Panel(id="a", widget="AgentTranscript", props={"agent_id": "abc-123"})
    assert resolve_title(p, AgentTranscript) == "Agent: abc-123"


def test_agent_transcript_without_agent_id_falls_through():
    p = Panel(id="a", widget="AgentTranscript")
    assert resolve_title(p, AgentTranscript) == "Agent"


def test_rich_transcript_default_includes_agent_id():
    p = Panel(id="r", widget="RichTranscript", props={"agent_id": "abc-123"})
    assert resolve_title(p, RichTranscript) == "Transcript: abc-123"


def test_rich_transcript_without_agent_id_falls_through():
    p = Panel(id="r", widget="RichTranscript")
    assert resolve_title(p, RichTranscript) == "Transcript"
