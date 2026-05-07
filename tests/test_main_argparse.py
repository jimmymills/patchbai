import sys

import pytest


def test_no_flag_passes_bypass_false(monkeypatch):
    from patchbai import __main__ as main_mod

    captured: dict = {}
    class _StubApp:
        def __init__(self, *, bypass_permissions: bool = False) -> None:
            captured["bypass_permissions"] = bypass_permissions
        def run(self): captured["ran"] = True

    monkeypatch.setattr(main_mod, "PatchbaiApp", _StubApp)
    monkeypatch.setattr(sys, "argv", ["patchbai"])
    rc = main_mod.main()
    assert rc == 0
    assert captured == {"bypass_permissions": False, "ran": True}


def test_bypass_flag_passes_bypass_true(monkeypatch):
    from patchbai import __main__ as main_mod

    captured: dict = {}
    class _StubApp:
        def __init__(self, *, bypass_permissions: bool = False) -> None:
            captured["bypass_permissions"] = bypass_permissions
        def run(self): pass

    monkeypatch.setattr(main_mod, "PatchbaiApp", _StubApp)
    monkeypatch.setattr(sys, "argv", ["patchbai", "--bypass-permissions"])
    main_mod.main()
    assert captured["bypass_permissions"] is True


def test_unknown_flag_exits_nonzero(monkeypatch, capsys):
    from patchbai import __main__ as main_mod
    monkeypatch.setattr(sys, "argv", ["patchbai", "--garbage"])
    with pytest.raises(SystemExit):
        main_mod.main()
