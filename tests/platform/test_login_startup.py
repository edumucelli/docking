from __future__ import annotations

import pytest

from docking.platform.desktop.implementations.unix.backend import UnixDesktopServices
from docking.platform.desktop.implementations.unix.startup import LinuxLoginStartup


def test_source_install_without_desktop_entry_has_no_login_startup(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        LinuxLoginStartup, "DESKTOP_ID", "org.docking.MissingLauncher.desktop"
    )

    assert UnixDesktopServices().login_startup() is None
    assert not (tmp_path / "autostart").exists()


@pytest.mark.parametrize(
    "disabled_key", ["Hidden=true", "X-GNOME-Autostart-enabled=false"]
)
def test_login_startup_can_reenable_entry_disabled_by_desktop(tmp_path, disabled_key):
    directory = tmp_path / "autostart"
    startup = LinuxLoginStartup(directory)
    entry = directory / startup.DESKTOP_ID

    assert not startup.enabled()
    assert not directory.exists()
    startup.set_enabled(True)
    assert startup.enabled()
    assert "\nExec=docking\nTryExec=docking\n" in entry.read_text()

    with entry.open("a", encoding="utf-8") as stream:
        stream.write(f"{disabled_key}\n")
    assert not startup.enabled()
    startup.set_enabled(True)
    assert startup.enabled()

    startup.set_enabled(False)
    startup.set_enabled(False)
    assert not entry.exists()


@pytest.mark.parametrize("symlink", [False, True])
def test_login_startup_preserves_entries_it_did_not_create(tmp_path, symlink):
    startup = LinuxLoginStartup(tmp_path)
    entry = tmp_path / startup.DESKTOP_ID
    target = tmp_path / "other.desktop" if symlink else entry
    target.write_text("[Desktop Entry]\nType=Application\nExec=docking\n")
    if symlink:
        entry.symlink_to(target)

    for enabled in (True, False):
        with pytest.raises(FileExistsError):
            startup.set_enabled(enabled)
    assert target.read_text() == "[Desktop Entry]\nType=Application\nExec=docking\n"
    assert entry.is_symlink() is symlink
