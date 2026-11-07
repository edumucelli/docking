"""Native Windows storage roots and explicit XDG overrides."""

import pytest

from docking.platform.environment import xdg
from docking.platform.host.implementations.windows.backend import WindowsHostServices


@pytest.fixture
def windows_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(xdg, "host_services", WindowsHostServices)
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


@pytest.mark.parametrize(
    "uri,expected",
    [
        (
            "file:///c:/Users/Example/r%C3%A9sum%C3%A9.txt",
            r"C:\Users\Example\résumé.txt",
        ),
        ("file://localhost/C:/a%20b", r"C:\a b"),
        ("file://server/share/a%20b", r"\\server\share\a b"),
        ("file:////server/share/a", r"\\server\share\a"),
        ("file:///c|/", "C:\\"),
        ("file:///C:/one%5Ctwo", r"C:\one\two"),
    ],
)
def test_windows_file_urls_decode_independently_of_the_host(uri, expected):
    from pathlib import Path

    host = WindowsHostServices()
    assert host.local_path(uri) == Path(expected)


@pytest.mark.parametrize(
    "uri",
    [
        "file:C:/relative",
        "file://user@server/share/a",
        "file:///1:/a",
        "file:///C:/a%00b",
    ],
)
def test_windows_file_urls_reject_invalid_paths(uri):
    assert WindowsHostServices().local_path(uri) is None
