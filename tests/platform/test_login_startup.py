from __future__ import annotations

import pytest
from gi.repository import Gio, GLib

from docking.platform.desktop.implementations.unix.backend import UnixDesktopServices
from docking.platform.desktop.implementations.unix.startup import LinuxLoginStartup
from docking.platform.host import selection as host_selection
from docking.platform.host.implementations.linux.backend import LinuxHostServices


@pytest.fixture
def native_linux(monkeypatch):
    monkeypatch.setattr(host_selection, "host_services", LinuxHostServices)


@pytest.mark.usefixtures("native_linux")
def test_source_install_without_desktop_entry_has_no_login_startup(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        LinuxLoginStartup, "DESKTOP_ID", "org.docking.MissingLauncher.desktop"
    )

    assert UnixDesktopServices().login_startup() is None
    assert not (tmp_path / "autostart").exists()


@pytest.mark.usefixtures("native_linux")
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


@pytest.mark.parametrize("has_legacy_entry", [False, True])
def test_login_startup_overrides_system_entry_without_changing_it(
    tmp_path, monkeypatch, has_legacy_entry
):
    system_config = tmp_path / "etc"
    system_entry = system_config / "autostart" / LinuxLoginStartup.DESKTOP_ID
    system_entry.parent.mkdir(parents=True)
    contents = "[Desktop Entry]\nType=Application\nName=Docking\nExec=/bin/sh\n"
    system_entry.write_text(contents)
    monkeypatch.setattr(GLib, "get_system_config_dirs", lambda: [str(system_config)])
    directory = tmp_path / "config" / "autostart"
    startup = LinuxLoginStartup(directory, command="/bin/sh", try_exec="/bin/sh")
    user_entry = directory / startup.DESKTOP_ID
    legacy_entry = directory / "docking.desktop"
    if has_legacy_entry:
        directory.mkdir(parents=True)
        legacy_entry.write_text(contents)

    assert startup.enabled()
    startup.set_enabled(False)
    assert not startup.enabled()
    assert "\nHidden=true\n" in user_entry.read_text()
    assert system_entry.read_text() == contents

    startup.set_enabled(True)
    assert startup.enabled()
    if has_legacy_entry:
        assert "\nHidden=true\n" in user_entry.read_text()
        assert "\nHidden=false\n" in legacy_entry.read_text()
    else:
        assert "\nHidden=" not in user_entry.read_text()
    startup.set_enabled(False)
    assert not startup.enabled()
    assert "\nHidden=true\n" in user_entry.read_text()
    if has_legacy_entry:
        assert "\nHidden=true\n" in legacy_entry.read_text()


@pytest.mark.parametrize("name", ["docking.desktop", LinuxLoginStartup.DESKTOP_ID])
def test_login_startup_reuses_an_existing_docking_entry(tmp_path, name):
    entry = tmp_path / name
    contents = (
        "[Desktop Entry]\nType=Application\nExec=docking\nHidden=false\n"
        "Name=Docking\nName[en_GB]=Docking\nX-MATE-Autostart-Delay=15\n"
    )
    entry.write_text(contents)
    entry.chmod(0o600)
    startup = LinuxLoginStartup(tmp_path, command="docking", try_exec="docking")

    assert startup.enabled()
    startup.set_enabled(True)
    assert entry.read_text() == contents
    assert list(tmp_path.iterdir()) == [entry]

    startup.set_enabled(False)
    assert not startup.enabled()
    assert entry.read_text() == contents.replace("Hidden=false", "Hidden=true")
    assert entry.stat().st_mode & 0o777 == 0o600

    startup.set_enabled(True)
    assert startup.enabled()
    assert entry.read_text() == contents
    assert list(tmp_path.iterdir()) == [entry]


@pytest.mark.parametrize("restriction", ["OnlyShowIn=KDE;", "NotShowIn=GNOME;"])
def test_login_startup_preserves_excluded_desktop_restrictions(
    tmp_path, monkeypatch, restriction
):
    show_in = Gio.DesktopAppInfo.get_show_in
    monkeypatch.setattr(
        Gio.DesktopAppInfo, "get_show_in", lambda app, _desktop: show_in(app, "GNOME")
    )
    entry = tmp_path / "docking.desktop"
    contents = (
        "[Desktop Entry]\nType=Application\nExec=/bin/sh\nName=Docking\n"
        f"{restriction}\n"
    )
    entry.write_text(contents)
    startup = LinuxLoginStartup(tmp_path, command="/bin/sh", try_exec="/bin/sh")

    assert not startup.enabled()
    with pytest.raises(OSError, match="excludes the current desktop"):
        startup.set_enabled(True)
    assert not startup.enabled()
    assert entry.read_text() == contents
    assert list(tmp_path.iterdir()) == [entry]


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
