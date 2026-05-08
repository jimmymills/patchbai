import sys

import pytest


def test_no_flag_passes_bypass_false(monkeypatch):
    from patchfeld import __main__ as main_mod

    captured: dict = {}
    class _StubApp:
        def __init__(self, *, bypass_permissions: bool = False) -> None:
            captured["bypass_permissions"] = bypass_permissions
        def run(self): captured["ran"] = True

    monkeypatch.setattr(main_mod, "PatchfeldApp", _StubApp)
    monkeypatch.setattr(sys, "argv", ["patchfeld"])
    rc = main_mod.main()
    assert rc == 0
    assert captured == {"bypass_permissions": False, "ran": True}


def test_bypass_flag_passes_bypass_true(monkeypatch):
    from patchfeld import __main__ as main_mod

    captured: dict = {}
    class _StubApp:
        def __init__(self, *, bypass_permissions: bool = False) -> None:
            captured["bypass_permissions"] = bypass_permissions
        def run(self): pass

    monkeypatch.setattr(main_mod, "PatchfeldApp", _StubApp)
    monkeypatch.setattr(sys, "argv", ["patchfeld", "--bypass-permissions"])
    main_mod.main()
    assert captured["bypass_permissions"] is True


def test_unknown_flag_exits_nonzero(monkeypatch, capsys):
    from patchfeld import __main__ as main_mod
    monkeypatch.setattr(sys, "argv", ["patchfeld", "--garbage"])
    with pytest.raises(SystemExit):
        main_mod.main()
