from pathlib import Path

import pytest

from patchbai.app import PatchbaiApp


def test_default_constructs_grants_object(tmp_path: Path):
    app = PatchbaiApp(cwd=tmp_path, global_dir=tmp_path / "cfg")
    assert app._permission_grants is not None
    assert app.manager._grants is app._permission_grants
    assert app.orchestrator.permission_grants is app._permission_grants


def test_bypass_permissions_skips_grants(tmp_path: Path):
    app = PatchbaiApp(
        cwd=tmp_path, global_dir=tmp_path / "cfg",
        bypass_permissions=True,
    )
    assert app._permission_grants is None
    assert app.manager._grants is None
    assert app.orchestrator.permission_grants is None
