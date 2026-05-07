import os
from pathlib import Path


def project_state_dir(cwd: Path) -> Path:
    return Path(cwd) / ".mod_tui"


def project_layout_path(cwd: Path) -> Path:
    return project_state_dir(cwd) / "layout.json"


def project_workspace_path(cwd: Path) -> Path:
    return project_state_dir(cwd) / "workspace.json"


def project_transcripts_dir(cwd: Path) -> Path:
    return project_state_dir(cwd) / "transcripts"


def project_transcript_path(cwd: Path, agent_id: str) -> Path:
    return project_transcripts_dir(cwd) / f"{agent_id}.jsonl"


def project_orchestrator_transcript(cwd: Path) -> Path:
    return project_transcripts_dir(cwd) / "orchestrator.jsonl"


def orchestrator_session_transcript_path(cwd: Path, session_id: str) -> Path:
    return project_transcripts_dir(cwd) / f"orchestrator.{session_id}.jsonl"


def global_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "mod_tui"
    return Path.home() / ".config" / "mod_tui"
