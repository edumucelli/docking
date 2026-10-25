"""Tests for the session applet."""

from types import SimpleNamespace

import docking.applets.session.applet as session_applet_mod
import docking.applets.session.unix as session_unix_mod
from docking.applets.session.applet import SessionApplet
from docking.applets.session.linux import LinuxSessionBackend
from docking.applets.session.state import _ACTIONS
from docking.core.config import Config


class TestSessionApplet:
    def test_creates_with_icon(self):
        applet = SessionApplet(48, config=Config())
        assert applet.item.icon is not None
        assert applet.item.name == "Session"

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = SessionApplet(size, config=Config())
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_menu_has_all_actions(self):
        applet = SessionApplet(48, config=Config())
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items]
        assert labels == [
            "Lock Screen",
            "Suspend",
            "",
            "Log Out",
            "Restart",
            "Shut Down",
        ]

    def test_actions_list_has_expected_entries(self):
        labels = [label for label, _cmd in _ACTIONS]
        assert "Lock Screen" in labels
        assert "Shut Down" in labels
        assert "Suspend" in labels

    def test_left_click_and_menu_dispatch_semantic_actions(self, monkeypatch):
        calls = []
        backend = SimpleNamespace(
            available_actions=lambda: frozenset(
                {"lock", "suspend", "logout", "reboot", "poweroff"}
            ),
            perform=lambda action: calls.append(action) or True,
            stop=lambda: calls.append("stop"),
        )
        monkeypatch.setattr(session_applet_mod, "create_backend", lambda: backend)
        applet = SessionApplet(48, config=Config())
        applet.on_clicked()
        items = applet.get_menu_items()
        for item in items:
            if item.get_label():
                callback, args = item._signals["activate"][0]
                callback(item, *args)
        applet.stop()
        assert calls == [
            "lock",
            "lock",
            "suspend",
            "logout",
            "reboot",
            "poweroff",
            "stop",
        ]

    def test_menu_disables_unavailable_actions(self, monkeypatch):
        backend = SimpleNamespace(
            available_actions=lambda: frozenset({"lock", "logout"})
        )
        monkeypatch.setattr(session_applet_mod, "create_backend", lambda: backend)
        applet = SessionApplet(48, config=Config())
        rows = [r for r in applet.get_menu_items() if r.get_label()]
        assert [r.get_sensitive() for r in rows] == [True, False, True, False, False]


class TestUnixSessionBackend:
    def test_lock_screen_prefers_screensaver_dbus(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)
        monkeypatch.setattr(
            session_unix_mod.shutil,
            "which",
            lambda cmd: "/usr/bin/gdbus" if cmd == "gdbus" else None,
        )

        seen: list[list[str]] = []

        def fake_run(cmd, capture_output, text, timeout, check):
            _ = (capture_output, text, timeout, check)
            seen.append(list(cmd))
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(session_unix_mod.subprocess, "run", fake_run)

        assert LinuxSessionBackend().perform("lock") is True
        assert seen == [
            [
                "/usr/bin/gdbus",
                "call",
                "--session",
                "--dest",
                "org.mate.ScreenSaver",
                "--object-path",
                "/org/mate/ScreenSaver",
                "--method",
                "org.mate.ScreenSaver.Lock",
            ]
        ]

    def test_lock_screen_prefers_explicit_session_id(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_ID", "2")
        monkeypatch.setattr(
            session_unix_mod.shutil,
            "which",
            lambda cmd: f"/usr/bin/{cmd}" if cmd in {"loginctl"} else None,
        )

        seen: list[list[str]] = []

        def fake_run(cmd, capture_output, text, timeout, check):
            _ = (capture_output, text, timeout, check)
            seen.append(list(cmd))
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(session_unix_mod.subprocess, "run", fake_run)

        assert LinuxSessionBackend().perform("lock") is True
        assert seen == [["loginctl", "lock-session", "2"]]

    def test_lock_screen_falls_back_to_screensaver(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)
        monkeypatch.setattr(
            session_unix_mod.shutil,
            "which",
            lambda cmd: (
                f"/usr/bin/{cmd}"
                if cmd in {"loginctl", "mate-screensaver-command"}
                else None
            ),
        )

        seen: list[list[str]] = []

        def fake_run(cmd, capture_output, text, timeout, check):
            _ = (capture_output, text, timeout, check)
            seen.append(list(cmd))
            if cmd[0] == "loginctl":
                return SimpleNamespace(returncode=1, stderr="no session")
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(session_unix_mod.subprocess, "run", fake_run)

        assert LinuxSessionBackend().perform("lock") is True
        assert seen == [["mate-screensaver-command", "-l"]]

    def test_lock_screen_returns_false_when_commands_missing_or_fail(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)
        monkeypatch.setattr(session_unix_mod.shutil, "which", lambda _cmd: None)

        assert LinuxSessionBackend().perform("lock") is False

        monkeypatch.setattr(session_unix_mod.shutil, "which", lambda _cmd: "/bin/x")

        def fail_run(cmd, capture_output, text, timeout, check):
            _ = (cmd, capture_output, text, timeout, check)
            return SimpleNamespace(returncode=1, stderr="no")

        monkeypatch.setattr(session_unix_mod.subprocess, "run", fail_run)
        assert LinuxSessionBackend().perform("lock") is False

    def test_lock_screen_continues_after_oserror(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)
        monkeypatch.setattr(
            session_unix_mod.shutil,
            "which",
            lambda cmd: (
                f"/usr/bin/{cmd}" if cmd in {"loginctl", "xdg-screensaver"} else None
            ),
        )
        seen: list[list[str]] = []

        def fake_run(cmd, capture_output, text, timeout, check):
            _ = (capture_output, text, timeout, check)
            seen.append(list(cmd))
            if cmd[0] == "xdg-screensaver":
                raise OSError("missing display")
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(session_unix_mod.subprocess, "run", fake_run)

        assert LinuxSessionBackend().perform("lock") is True
        assert seen == [["xdg-screensaver", "lock"], ["loginctl", "lock-session"]]

    def test_lock_screen_uses_host_commands_in_flatpak(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_ID", "2")
        monkeypatch.setattr(
            session_unix_mod.flatpak,
            "spawn_path",
            lambda **_: "/usr/bin/flatpak-spawn",
        )
        monkeypatch.setattr(session_unix_mod.shutil, "which", lambda _cmd: None)
        seen: list[list[str]] = []

        def fake_run(cmd, capture_output, text, timeout, check):
            _ = (capture_output, text, timeout, check)
            seen.append(list(cmd))
            if "loginctl" in cmd:
                return SimpleNamespace(returncode=0, stderr="")
            return SimpleNamespace(returncode=1, stderr="missing")

        monkeypatch.setattr(session_unix_mod.subprocess, "run", fake_run)

        assert LinuxSessionBackend().perform("lock") is True
        assert seen[0] == [
            "/usr/bin/flatpak-spawn",
            "--host",
            "env",
            "-u",
            "GIO_USE_VFS",
            "-u",
            "GI_TYPELIB_PATH",
            "-u",
            "GSETTINGS_SCHEMA_DIR",
            "-u",
            "XDG_DATA_DIRS",
            "mate-screensaver-command",
            "-l",
        ]
        assert seen[-1] == [
            "/usr/bin/flatpak-spawn",
            "--host",
            "env",
            "-u",
            "GIO_USE_VFS",
            "-u",
            "GI_TYPELIB_PATH",
            "-u",
            "GSETTINGS_SCHEMA_DIR",
            "-u",
            "XDG_DATA_DIRS",
            "loginctl",
            "lock-session",
            "2",
        ]

    def test_run_logs_popen_failure(self, monkeypatch):
        launched: list[list[str]] = []
        monkeypatch.setattr(
            session_unix_mod.subprocess,
            "Popen",
            lambda cmd, **_kwargs: launched.append(list(cmd)),
        )
        LinuxSessionBackend().perform("suspend")
        assert launched == [["systemctl", "suspend"]]

        monkeypatch.setattr(
            session_unix_mod.subprocess,
            "Popen",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")),
        )
        LinuxSessionBackend().perform("suspend")

    def test_run_uses_host_command_in_flatpak(self, monkeypatch):
        launched: list[list[str]] = []
        monkeypatch.setattr(
            session_unix_mod.flatpak,
            "spawn_path",
            lambda **_: "/usr/bin/flatpak-spawn",
        )
        monkeypatch.setattr(
            session_unix_mod.subprocess,
            "Popen",
            lambda cmd, **_kwargs: launched.append(list(cmd)),
        )

        LinuxSessionBackend().perform("suspend")

        assert launched == [
            [
                "/usr/bin/flatpak-spawn",
                "--host",
                "env",
                "-u",
                "GIO_USE_VFS",
                "-u",
                "GI_TYPELIB_PATH",
                "-u",
                "GSETTINGS_SCHEMA_DIR",
                "-u",
                "XDG_DATA_DIRS",
                "systemctl",
                "suspend",
            ]
        ]
