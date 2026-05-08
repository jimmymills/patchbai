from patchfeld.config import Config, ConfigStore


def test_widgets_section_defaults_to_enabled():
    cfg = Config()
    assert cfg.widgets.local_dir_enabled is True


def test_get_path_widgets_local_dir_enabled():
    cfg = Config()
    assert cfg.get_path("widgets.local_dir_enabled") is True


def test_set_path_widgets_local_dir_enabled(tmp_path):
    store = ConfigStore(global_dir=tmp_path)
    cfg = store.load()
    cfg.set_path("widgets.local_dir_enabled", False)
    store.save(cfg)
    reloaded = store.load()
    assert reloaded.widgets.local_dir_enabled is False
