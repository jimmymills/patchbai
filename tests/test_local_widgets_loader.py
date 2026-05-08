from pathlib import Path

import pytest

from patchbai.layout.local_widgets import LocalWidgetLoader, LoadOutcome
from patchbai.layout.registry import WidgetRegistry


def _write(p: Path, body: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_loader_registers_single_widget(tmp_path):
    _write(tmp_path / "banner.py", """
from textual.widgets import Static

class Banner(Static):
    pass
""")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert [o.status for o in outcomes] == ["ok"]
    assert reg.get("Banner").__name__ == "Banner"
    assert reg.describe("Banner").source == "local"


def test_loader_uses_metadata_name_description_props(tmp_path):
    _write(tmp_path / "spark.py", """
from textual.widgets import Static

__patchbai_widget__ = {
    "name": "Sparkline",
    "description": "Token sparkline.",
    "props_schema": {"agent_id": str},
}

class Sparkline(Static):
    pass
""")
    reg = WidgetRegistry()
    LocalWidgetLoader(tmp_path, reg).load()
    info = reg.describe("Sparkline")
    assert info.description == "Token sparkline."
    assert info.props_schema == {"agent_id": str}


def test_loader_honors_entry_point_metadata(tmp_path):
    _write(tmp_path / "x.py", """
from textual.widgets import Static

class Real(Static):
    pass

class Decoy(Static):
    pass

__patchbai_widget__ = {"name": "X", "entry_point": Real}
""")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert outcomes[0].status == "ok"
    assert reg.get("X").__name__ == "Real"


def test_loader_honors_widget_class_sentinel(tmp_path):
    _write(tmp_path / "y.py", """
from textual.widgets import Static

class Picked(Static):
    pass

class Other(Static):
    pass

WIDGET_CLASS = Picked
""")
    reg = WidgetRegistry()
    LocalWidgetLoader(tmp_path, reg).load()
    assert reg.get("Y").__name__ == "Picked"


def test_loader_pascal_cases_filename_stem(tmp_path):
    _write(tmp_path / "git_status.py", """
from textual.widgets import Static
class GitStatus(Static):
    pass
""")
    reg = WidgetRegistry()
    LocalWidgetLoader(tmp_path, reg).load()
    assert "GitStatus" in reg.known()


def test_loader_skips_underscore_and_dot_files(tmp_path):
    _write(tmp_path / "_hidden.py", "x = 1")
    _write(tmp_path / ".dot.py", "x = 1")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert outcomes == []


def test_loader_missing_dir_returns_empty(tmp_path):
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path / "does_not_exist", reg).load()
    assert outcomes == []


def test_loader_records_import_error(tmp_path):
    _write(tmp_path / "broken.py", "this is not valid python\n")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert len(outcomes) == 1
    assert outcomes[0].status == "import_error"
    assert outcomes[0].error and "SyntaxError" in outcomes[0].error
    assert "broken" not in reg.known() and "Broken" not in reg.known()


def test_loader_records_no_widget_class(tmp_path):
    _write(tmp_path / "nowidget.py", "x = 42\n")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert outcomes[0].status == "no_widget_class"


def test_loader_records_ambiguous_class(tmp_path):
    _write(tmp_path / "two.py", """
from textual.widgets import Static
class A(Static):
    pass
class B(Static):
    pass
""")
    reg = WidgetRegistry()
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert outcomes[0].status == "ambiguous_class"


def test_loader_skips_name_collision_with_builtin(tmp_path):
    from textual.widgets import Static
    reg = WidgetRegistry()
    reg.register("OrchestratorChat", Static)  # builtin (default source)

    _write(tmp_path / "evil.py", """
from textual.widgets import Static

__patchbai_widget__ = {"name": "OrchestratorChat"}

class Evil(Static):
    pass
""")
    outcomes = LocalWidgetLoader(tmp_path, reg).load()
    assert outcomes[0].status == "name_collision"
    # Builtin still wins.
    assert reg.get("OrchestratorChat") is Static
