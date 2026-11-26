from __future__ import annotations

import pytest
from gi.repository import Gio, GLib

from docking.platform.desktop.implementations.unix.backend import UnixDesktopServices
from docking.platform.desktop.implementations.unix.startup import LinuxLoginStartup
from docking.platform.host import selection as host_selection
from docking.platform.host.implementations.linux.backend import LinuxHostServices


@pytest.fixture(autouse=True)
def isolate_system_autostart(monkeypatch):
    monkeypatch.setattr(GLib, "get_system_config_dirs", list)


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
@pytest.mark.parametrize("had_managed_entry", [False, True])
def test_login_startup_uses_installed_absolute_launcher(
    tmp_path, monkeypatch, had_managed_entry
):
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

    autostart = tmp_path / "autostart"
    entry_path = autostart / desktop.name
    if had_managed_entry:
        removed_launcher = str(tmp_path / "old-profile" / "docking")
        LinuxLoginStartup(
            autostart, command=removed_launcher, try_exec=removed_launcher
        ).set_enabled(True)
        with entry_path.open("a", encoding="utf-8") as stream:
            stream.write("X-MATE-Autostart-Delay=15\n")
        entry_path.chmod(0o600)

    startup = UnixDesktopServices().login_startup()
    assert startup is not None
    assert startup.enabled() is had_managed_entry
    if not had_managed_entry:
        startup.set_enabled(True)
    entry = Gio.DesktopAppInfo.new_from_filename(str(entry_path))
    assert entry is not None
    assert entry.get_string("Exec") == command
    assert entry.get_string("TryExec") == str(launcher)
    if had_managed_entry:
        assert entry.get_string("X-MATE-Autostart-Delay") == "15"
        assert entry_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("stale_key", ["Exec", "TryExec"])
def test_enabling_login_startup_replaces_a_stale_managed_command(tmp_path, stale_key):
    startup = LinuxLoginStartup(tmp_path, command="/bin/sh", try_exec="/bin/sh")
    startup.set_enabled(True)
    entry = tmp_path / startup.DESKTOP_ID
    removed_launcher = tmp_path / "old-profile" / "docking"
    previous = entry.read_text().replace(
        f"{stale_key}=/bin/sh", f"{stale_key}={removed_launcher}"
    )
    entry.write_text(previous + "X-MATE-Autostart-Delay=15\n")

    assert not startup.enabled()
    startup.set_enabled(True)
    assert startup.enabled()
    assert f"{stale_key}=/bin/sh" in entry.read_text()
    assert "X-MATE-Autostart-Delay=15" in entry.read_text()


def test_login_startup_refresh_preserves_a_disabled_entry(tmp_path):
    old = str(tmp_path / "old-profile" / "docking")
    LinuxLoginStartup(tmp_path, command=old, try_exec=old).set_enabled(True)
    entry = tmp_path / LinuxLoginStartup.DESKTOP_ID
    previous = entry.read_text() + "Hidden=true\n"
    entry.write_text(previous)

    startup = LinuxLoginStartup(tmp_path, command="/bin/sh", try_exec="/bin/sh")
    startup.refresh_launcher()

    assert not startup.enabled()
    assert entry.read_text() == previous


def test_login_startup_overrides_system_entry_without_changing_it(
    tmp_path, monkeypatch
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


@pytest.mark.parametrize("restriction", ["OnlyShowIn=KDE;", "NotShowIn=GNOME;"])
def test_login_startup_preserves_excluded_desktop_restrictions(
    tmp_path, monkeypatch, restriction
):
    show_in = Gio.DesktopAppInfo.get_show_in
    monkeypatch.setattr(
        Gio.DesktopAppInfo, "get_show_in", lambda app, _desktop: show_in(app, "GNOME")
    )
    startup = LinuxLoginStartup(tmp_path, command="/bin/sh", try_exec="/bin/sh")
    startup.set_enabled(True)
    entry = tmp_path / startup.DESKTOP_ID
    contents = entry.read_text() + f"{restriction}\n"
    entry.write_text(contents)

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
    contents = "[Desktop Entry]\nType=Application\nName=Docking\nExec=docking\n"
    target.write_text(contents)
    if symlink:
        entry.symlink_to(target)

    for enabled in (True, False):
        with pytest.raises(FileExistsError):
            startup.set_enabled(enabled)
    assert target.read_text() == contents
    assert entry.is_symlink() is symlink
