"""Tests for the session applet."""

from types import SimpleNamespace

import docking.applets.session.applet as session_applet_mod
import docking.applets.session.state as session_state_mod
from docking.applets.session.applet import SessionApplet
from docking.applets.session.state import _ACTIONS, lock_screen


class TestSessionApplet:
    def test_creates_with_icon(self):
        applet = SessionApplet(48)
        assert applet.item.icon is not None
        assert applet.item.name == "Session"

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = SessionApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_menu_has_all_actions(self):
        applet = SessionApplet(48)
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items]
        assert labels == [label for label, _cmd in _ACTIONS]

    def test_actions_list_has_expected_entries(self):
        labels = [label for label, _cmd in _ACTIONS]
        assert "Lock Screen" in labels
        assert "Shut Down" in labels
        assert "Suspend" in labels

    def test_left_click_locks_screen(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            session_applet_mod,
            "lock_screen",
            lambda: calls.append("lock") or True,
        )
        applet = SessionApplet(48)
        applet.on_clicked()
        assert calls == ["lock"]

    def test_lock_menu_item_uses_lock_helper(self, monkeypatch):
        class _FakeMenuItem:
            def __init__(self, label: str = "") -> None:
                self._label = label
                self._signals: dict[str, tuple[object, tuple[object, ...]]] = {}

            def get_label(self) -> str:
                return self._label

            def connect(self, signal: str, callback, *args) -> None:
                self._signals[signal] = (callback, args)

            def activate(self) -> None:
                callback, args = self._signals["activate"]
                callback(None, *args)

        monkeypatch.setattr(
            session_applet_mod,
            "Gtk",
            SimpleNamespace(MenuItem=_FakeMenuItem),
        )

        calls: list[str] = []
        monkeypatch.setattr(
            session_applet_mod,
            "lock_screen",
            lambda: calls.append("lock") or True,
        )
        monkeypatch.setattr(
            session_applet_mod,
            "_run",
            lambda **kwargs: calls.append("run"),
        )

        applet = SessionApplet(48)
        items = applet.get_menu_items()
        items[0].activate()
        items[1].activate()
        assert calls == ["lock", "run"]


class TestSessionState:
    def test_lock_screen_prefers_explicit_session_id(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_ID", "2")
        monkeypatch.setattr(
            session_state_mod.shutil,
            "which",
            lambda cmd: f"/usr/bin/{cmd}" if cmd in {"loginctl"} else None,
        )

        seen: list[list[str]] = []

        def fake_run(cmd, capture_output, text, timeout, check):
            _ = (capture_output, text, timeout, check)
            seen.append(list(cmd))
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(session_state_mod.subprocess, "run", fake_run)

        assert lock_screen() is True
        assert seen == [["loginctl", "lock-session", "2"]]

    def test_lock_screen_falls_back_to_screensaver(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)
        monkeypatch.setattr(
            session_state_mod.shutil,
            "which",
            lambda cmd: f"/usr/bin/{cmd}"
            if cmd in {"loginctl", "mate-screensaver-command"}
            else None,
        )

        seen: list[list[str]] = []

        def fake_run(cmd, capture_output, text, timeout, check):
            _ = (capture_output, text, timeout, check)
            seen.append(list(cmd))
            if cmd[0] == "loginctl":
                return SimpleNamespace(returncode=1, stderr="no session")
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(session_state_mod.subprocess, "run", fake_run)

        assert lock_screen() is True
        assert seen == [["mate-screensaver-command", "-l"]]
