from __future__ import annotations

import pytest
from gi.repository import Gio, GLib

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


def test_login_startup_uses_installed_absolute_launcher(tmp_path, monkeypatch):
    launcher = tmp_path / "profile path" / "docking"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o755)
    desktop = tmp_path / LinuxLoginStartup.DESKTOP_ID
    command = f'"{launcher}" --option'
    desktop.write_text(
        f"[Desktop Entry]\nType=Application\nName=Docking\nExec={command}\n"
    )
    installed = Gio.DesktopAppInfo.new_from_filename(str(desktop))
    assert installed is not None
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(Gio.DesktopAppInfo, "new", lambda _name: installed)

    startup = UnixDesktopServices().login_startup()
    assert startup is not None
    startup.set_enabled(True)
    entry = Gio.DesktopAppInfo.new_from_filename(
        str(tmp_path / "autostart" / desktop.name)
    )
    assert entry is not None
    assert entry.get_string("Exec") == command
    assert entry.get_string("TryExec") == str(launcher)


def test_login_startup_overrides_system_entry_without_changing_it(
    tmp_path, monkeypatch
):
    system_config = tmp_path / "etc"
    system_entry = system_config / "autostart" / LinuxLoginStartup.DESKTOP_ID
    system_entry.parent.mkdir(parents=True)
    contents = "[Desktop Entry]\nType=Application\nName=Docking\nExec=/bin/true\n"
    system_entry.write_text(contents)
    monkeypatch.setattr(GLib, "get_system_config_dirs", lambda: [str(system_config)])
    directory = tmp_path / "config" / "autostart"
    startup = LinuxLoginStartup(directory, command="/bin/true", try_exec="/bin/true")
    user_entry = directory / startup.DESKTOP_ID

    assert startup.enabled()
    startup.set_enabled(False)
    assert not startup.enabled()
    assert "\nHidden=true\n" in user_entry.read_text()
    assert system_entry.read_text() == contents

    startup.set_enabled(True)
    assert startup.enabled()
    assert "\nHidden=" not in user_entry.read_text()
    startup.set_enabled(False)
    assert not startup.enabled()
    assert "\nHidden=true\n" in user_entry.read_text()


@pytest.mark.parametrize(
    "disabled_key", ["Hidden=true", "X-GNOME-Autostart-enabled=false"]
)
def test_login_startup_can_reenable_entry_disabled_by_desktop(tmp_path, disabled_key):
    directory = tmp_path / "autostart"
    startup = LinuxLoginStartup(directory, command="docking", try_exec="docking")
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
    startup = LinuxLoginStartup(tmp_path, command="docking", try_exec="docking")
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
