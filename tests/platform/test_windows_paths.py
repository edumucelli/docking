"""Native Windows storage roots and explicit XDG overrides."""

from types import SimpleNamespace

import pytest

from docking.platform.environment import xdg


@pytest.fixture
def windows_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(xdg, "sys", SimpleNamespace(platform="win32"))
    for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    return tmp_path


def test_windows_config_uses_roaming_and_cache_uses_local(windows_paths):
    assert xdg.docking_config_dir() == windows_paths / "Roaming" / "docking"
    assert xdg.docking_cache_dir() == windows_paths / "Local" / "docking"
    assert xdg.docking_data_dir() != xdg.docking_config_dir()
    assert xdg.docking_state_dir() != xdg.docking_cache_dir()


def test_explicit_xdg_paths_still_win_on_windows(windows_paths, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(windows_paths / "isolated"))
    assert xdg.docking_config_dir() == windows_paths / "isolated" / "docking"
    assert xdg.xdg_data_home() == windows_paths / "Roaming" / "Data"


def test_native_windows_detection_ignores_inherited_unix_session(monkeypatch):
    from docking.platform.environment import environment

    monkeypatch.setattr(environment, "is_windows", lambda: True)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    assert environment.detect_desktop() is environment.Desktop.WINDOWS
    assert not environment.Desktop.WINDOWS.uses_monitor_geometry
