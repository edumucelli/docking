"""Tests for notifications applet and backend helpers."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from typing import ClassVar
from unittest.mock import MagicMock

import docking.applets.notifications.applet as notifications_applet_mod
import docking.applets.notifications.state as notifications_state_mod
from docking.applets.notifications.applet import (
    HISTORY_LIMIT,
    NotificationEntry,
    NotificationsApplet,
)
from docking.applets.notifications.render import create_notifications_icon
from docking.applets.notifications.state import (
    DunstBackend,
    GnomeBackend,
    NotificationsState,
    NullBackend,
    detect_backend,
    tooltip_text,
    unavailable_state,
)


def _state(**overrides: object) -> NotificationsState:
    base = NotificationsState(
        available=True,
        backend="dunstctl",
        paused=False,
        pending=3,
        pending_known=True,
    )
    values = {
        field: getattr(base, field) for field in NotificationsState.__dataclass_fields__
    }
    values.update(overrides)
    return NotificationsState(**values)


class TestTooltipText:
    def test_unavailable(self):
        assert "No backend" in tooltip_text(unavailable_state())

    def test_contains_pending(self):
        text = tooltip_text(_state(paused=True, pending=5, pending_known=True))
        assert "Pending: 5" in text

    def test_hides_pending_when_unknown(self):
        text = tooltip_text(_state(pending_known=False))
        assert "Pending" not in text
        assert text == "Notifications"


class TestStateParsing:
    def test_parse_bool_values(self):
        assert notifications_state_mod._parse_bool("true") is True
        assert notifications_state_mod._parse_bool("false") is False
        assert notifications_state_mod._parse_bool(None) is None
        assert notifications_state_mod._parse_bool("invalid") is None

    def test_parse_pending_count_values(self):
        assert notifications_state_mod._parse_pending_count("7") == 7
        assert (
            notifications_state_mod._parse_pending_count(
                '{"displayed": 0, "history": 2, "waiting": 4}'
            )
            == 4
        )
        assert notifications_state_mod._parse_pending_count("oops") is None
        assert notifications_state_mod._parse_pending_count("") is None
        assert notifications_state_mod._parse_pending_count('{"waiting": -5}') == 0
        assert notifications_state_mod._parse_pending_count("[]") is None

    def test_pending_badge_count(self):
        assert notifications_state_mod.pending_badge_count(unavailable_state()) == 0
        assert (
            notifications_state_mod.pending_badge_count(_state(pending_known=False))
            == 0
        )
        assert notifications_state_mod.pending_badge_count(_state(pending=4)) == 4
        assert notifications_state_mod.pending_badge_count(_state(pending=999)) == 99

    def test_run_and_has_command_helpers(self, monkeypatch):
        class _Proc:
            def __init__(self, code=0, out=""):
                self.returncode = code
                self.stdout = out

        monkeypatch.setattr(notifications_state_mod, "is_flatpak", lambda: False)
        monkeypatch.setattr(
            notifications_state_mod.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(0, "ok\n"),
        )
        assert notifications_state_mod._run(["echo"]) == "ok"

        monkeypatch.setattr(
            notifications_state_mod.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(1, "no"),
        )
        assert notifications_state_mod._run(["echo"]) is None

        def fail_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)

        monkeypatch.setattr(notifications_state_mod.subprocess, "run", fail_run)
        assert notifications_state_mod._run(["echo"]) is None

        monkeypatch.setattr(notifications_state_mod.shutil, "which", lambda cmd: None)
        assert notifications_state_mod._has_command("x") is False
        monkeypatch.setattr(
            notifications_state_mod.shutil, "which", lambda cmd: "/usr/bin/x"
        )
        assert notifications_state_mod._has_command("x") is True

    def test_run_prefers_host_command_in_flatpak(self, monkeypatch):
        class _Proc:
            returncode = 0
            stdout = "false\n"

        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _Proc()

        monkeypatch.setattr(notifications_state_mod, "is_flatpak", lambda: True)
        monkeypatch.setattr(
            notifications_state_mod.shutil,
            "which",
            lambda cmd: "/usr/bin/flatpak-spawn" if cmd == "flatpak-spawn" else None,
        )
        monkeypatch.setattr(notifications_state_mod.subprocess, "run", fake_run)

        assert (
            notifications_state_mod._run(["gsettings", "get", "schema", "key"])
            == "false"
        )
        assert calls == [
            [
                "/usr/bin/flatpak-spawn",
                "--host",
                "gsettings",
                "get",
                "schema",
                "key",
            ]
        ]

    def test_has_command_checks_host_in_flatpak(self, monkeypatch):
        class _Proc:
            returncode = 0
            stdout = "/usr/bin/dunstctl\n"

        monkeypatch.setattr(notifications_state_mod, "is_flatpak", lambda: True)
        monkeypatch.setattr(
            notifications_state_mod.shutil,
            "which",
            lambda cmd: "/usr/bin/flatpak-spawn" if cmd == "flatpak-spawn" else None,
        )
        monkeypatch.setattr(
            notifications_state_mod.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(),
        )

        assert notifications_state_mod._has_command("dunstctl") is True

    def test_dunst_backend_get_state(self, monkeypatch):
        def fake_run(cmd: list[str], timeout_s: float = 2.0) -> str | None:
            _ = timeout_s
            table = {
                ("dunstctl", "is-paused"): "false",
                ("dunstctl", "count", "waiting"): "6",
            }
            return table.get(tuple(cmd))

        monkeypatch.setattr(notifications_state_mod, "_run", fake_run)
        state = DunstBackend().get_state()
        assert state.available is True
        assert state.paused is False
        assert state.pending == 6
        assert state.pending_known is True

    def test_dunst_backend_falls_back_to_json_count(self, monkeypatch):
        def fake_run(cmd: list[str], timeout_s: float = 2.0) -> str | None:
            _ = timeout_s
            table = {
                ("dunstctl", "is-paused"): "true",
                ("dunstctl", "count", "waiting"): None,
                ("dunstctl", "count"): '{"displayed": 0, "history": 3, "waiting": 2}',
            }
            return table.get(tuple(cmd))

        monkeypatch.setattr(notifications_state_mod, "_run", fake_run)
        state = DunstBackend().get_state()
        assert state.available is True
        assert state.paused is True
        assert state.pending == 2
        assert state.pending_known is True

    def test_gnome_backend_get_state(self, monkeypatch):
        monkeypatch.setattr(
            notifications_state_mod,
            "_run",
            lambda cmd, timeout_s=2.0: (
                "false"
                if cmd[:3] == ["gsettings", "get", "org.gnome.desktop.notifications"]
                else None
            ),
        )
        state = GnomeBackend().get_state()
        assert state.available is True
        assert state.paused is True
        assert state.pending_known is False

    def test_backend_command_methods_and_null_backend(self, monkeypatch):
        monkeypatch.setattr(
            notifications_state_mod,
            "_run",
            lambda cmd, timeout_s=2.0: "",
        )
        assert DunstBackend().set_paused(True) is True
        assert DunstBackend().clear_notifications() is True
        assert GnomeBackend().set_paused(False) is True
        assert GnomeBackend().clear_notifications() is False
        null = NullBackend()
        assert null.get_state().available is False
        assert null.set_paused(True) is False
        assert null.clear_notifications() is False

    def test_gnome_and_dunst_unavailable_paths(self, monkeypatch):
        monkeypatch.setattr(
            notifications_state_mod,
            "_run",
            lambda cmd, timeout_s=2.0: "maybe" if cmd[0] == "dunstctl" else None,
        )
        assert DunstBackend().get_state().available is False
        assert GnomeBackend().get_state().available is False


class TestBackendDetection:
    def test_detect_backend_prefers_dunst(self, monkeypatch):
        monkeypatch.setattr(
            notifications_state_mod,
            "_has_command",
            lambda cmd: cmd in {"dunstctl", "gsettings"},
        )
        monkeypatch.setattr(
            notifications_state_mod.DunstBackend,
            "get_state",
            lambda self: _state(),
        )
        backend = detect_backend()
        assert isinstance(backend, DunstBackend)

    def test_detect_backend_falls_back_to_gnome(self, monkeypatch):
        monkeypatch.setattr(notifications_state_mod, "_has_command", lambda cmd: True)
        monkeypatch.setattr(
            notifications_state_mod.DunstBackend,
            "get_state",
            lambda self: unavailable_state(),
        )
        monkeypatch.setattr(
            notifications_state_mod.GnomeBackend,
            "get_state",
            lambda self: _state(backend="gnome", pending_known=False),
        )
        backend = detect_backend()
        assert isinstance(backend, GnomeBackend)

    def test_detect_backend_returns_null_when_none_available(self, monkeypatch):
        monkeypatch.setattr(notifications_state_mod, "_has_command", lambda cmd: False)
        backend = detect_backend()
        assert isinstance(backend, NullBackend)


class _StubBackend:
    def __init__(self, state: NotificationsState, supports_clear: bool = True) -> None:
        self._state = state
        self.supports_clear = supports_clear
        self.set_paused_calls: list[bool] = []
        self.clear_calls = 0

    def get_state(self) -> NotificationsState:
        return self._state

    def set_paused(self, paused: bool) -> bool:
        self.set_paused_calls.append(paused)
        self._state = replace(self._state, paused=paused)
        return True

    def clear_notifications(self) -> bool:
        self.clear_calls += 1
        self._state = replace(self._state, pending=0)
        return True


def _make_applet(
    monkeypatch,
    state: NotificationsState,
    supports_clear: bool = True,
) -> tuple[NotificationsApplet, _StubBackend]:
    backend = _StubBackend(state=state, supports_clear=supports_clear)
    monkeypatch.setattr(notifications_applet_mod, "detect_backend", lambda: backend)
    return NotificationsApplet(48), backend


class TestNotificationsApplet:
    def test_creates_with_icon(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        assert applet.item.icon is not None

    def test_click_toggles_dnd(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state(paused=False))
        applet.on_clicked()
        assert backend.set_paused_calls == [True]
        assert applet._state.paused is True

    def test_click_noop_when_unavailable_or_backend_rejects(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, unavailable_state())
        applet.on_clicked()
        assert backend.set_paused_calls == []

        applet2, backend2 = _make_applet(monkeypatch, _state(paused=False))
        backend2.set_paused = lambda paused: False  # type: ignore[method-assign]
        applet2.present = MagicMock()
        applet2.on_clicked()
        applet2.present.assert_not_called()

    def test_unavailable_menu(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, unavailable_state())
        items = applet.get_menu_items()
        assert len(items) == 1
        assert "No notification backend" in items[0].get_label()
        assert items[0].get_sensitive() is False

    def test_menu_includes_pending_and_clear(self, monkeypatch):
        applet, _backend = _make_applet(
            monkeypatch,
            _state(pending=8, pending_known=True),
            supports_clear=True,
        )
        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]
        assert "Do Not Disturb" in labels
        assert "Pending: 8" in labels
        assert "Clear History" in labels
        assert "Clear Notifications" in labels

    def test_clear_updates_pending(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state(pending=5))
        applet._on_clear()
        assert backend.clear_calls == 1
        assert applet._state.pending == 0

    def test_clear_history_clears_stored_notifications(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._history = [
            NotificationEntry(app_name="Mail", summary="A", body="B"),
            NotificationEntry(app_name="Chat", summary="C", body="D"),
        ]
        applet._history_index = 1
        applet.present = MagicMock()

        applet._on_clear_history()

        assert applet._history == []
        assert applet._history_index == 0
        applet.present.assert_called_once()

    def test_poll_result_refreshes_only_on_change(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending=1))
        applet.present = MagicMock()
        assert applet._on_poll_result(_state(pending=1)) is False
        applet.present.assert_not_called()
        assert applet._on_poll_result(_state(pending=2)) is False
        applet.present.assert_called_once()

    def test_activity_badge_shows_for_unknown_pending(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        applet._activity_until_monotonic = float("inf")
        assert applet._show_activity_badge() is True

    def test_activity_badge_hidden_when_pending_known(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=True))
        applet._activity_until_monotonic = float("inf")
        assert applet._show_activity_badge() is False

    def test_history_badge_count_uses_history_size(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._history = [NotificationEntry("a", "b", "c")] * 4
        assert applet._history_badge_count() == 4

    def test_create_icon_passes_history_badge_count(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._history = [NotificationEntry("a", "b", "c")] * 3
        captured: dict[str, object] = {}

        def fake_create_notifications_icon(**kwargs):
            captured.update(kwargs)
            return

        monkeypatch.setattr(
            notifications_applet_mod,
            "create_notifications_icon",
            fake_create_notifications_icon,
        )
        applet.create_icon(size=48)
        assert captured["badge_count"] == 3

    def test_notification_activity_refreshes_presentation(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        monkeypatch.setattr(notifications_applet_mod.time, "monotonic", lambda: 100.0)
        monkeypatch.setattr(
            notifications_applet_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _cb: 1,
        )
        applet.present = MagicMock()

        assert applet._on_notification_activity() is False
        assert applet._activity_until_monotonic == 108.0
        applet.present.assert_called_once()

    def test_notification_event_updates_last_content(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        monkeypatch.setattr(notifications_applet_mod.time, "monotonic", lambda: 5.0)
        monkeypatch.setattr(
            notifications_applet_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _cb: 1,
        )
        applet.present = MagicMock()

        assert (
            applet._on_notification_event("Mail", "New message", "Hello world") is False
        )
        assert len(applet._history) == 1
        assert applet._history[0] == NotificationEntry(
            app_name="Mail",
            summary="New message",
            body="Hello world",
        )
        assert applet._history_index == 0
        applet.present.assert_called_once()

    def test_refresh_tooltip_includes_last_notification(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        applet._history = [
            NotificationEntry(
                app_name="Mail", summary="New message", body="Body content"
            )
        ]
        applet._history_index = 0

        applet.refresh_tooltip()

        assert "Notification 1/1:" in applet.item.name
        assert "New message" in applet.item.name
        assert "App: Mail" in applet.item.name

    def test_extract_monitor_string(self):
        assert (
            NotificationsApplet._extract_monitor_string('   string "Docking test"')
            == "Docking test"
        )
        assert NotificationsApplet._extract_monitor_string("   int32 2000") is None

    def test_scroll_iterates_notification_history(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        applet._history = [
            NotificationEntry(app_name="A", summary="First", body=""),
            NotificationEntry(app_name="B", summary="Second", body=""),
            NotificationEntry(app_name="C", summary="Third", body=""),
        ]
        applet._history_index = 0
        applet.present = MagicMock()

        applet.on_scroll(direction_up=True)
        assert applet._history_index == 1

        applet.on_scroll(direction_up=False)
        assert applet._history_index == 0
        assert applet.present.call_count == 2

    def test_scroll_wraps_history(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        applet._history = [
            NotificationEntry(app_name="A", summary="First", body=""),
            NotificationEntry(app_name="B", summary="Second", body=""),
        ]
        applet._history_index = 0
        applet.on_scroll(direction_up=False)
        assert applet._history_index == 1

    def test_start_stop_tick_and_toggle_dnd(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        started: list[str] = []
        removed: list[int] = []
        applet._start_activity_monitor = lambda: started.append("monitor")  # type: ignore[assignment]
        applet._stop_activity_monitor = lambda: started.append("stop-monitor")  # type: ignore[assignment]
        monkeypatch.setattr(
            notifications_applet_mod.GLib,
            "timeout_add_seconds",
            lambda sec, cb: 10,
        )
        monkeypatch.setattr(
            notifications_applet_mod.GLib,
            "source_remove",
            lambda source_id: removed.append(source_id),
        )
        applet.start(notify=lambda: None)
        assert applet._timer_id == 10
        assert started == ["monitor"]

        applet._activity_clear_id = 12
        applet.stop()
        assert applet._timer_id == 0
        assert applet._activity_clear_id == 0
        assert started[-1] == "stop-monitor"
        assert removed == [12, 10]

        def fake_run_guarded(*, key, name, fn, on_result=None, on_error=None):
            _ = key, name, on_error
            result = fn()
            if on_result is not None:
                on_result(result)
            return True

        applet._worker.run_guarded = fake_run_guarded  # type: ignore[method-assign]
        applet._poll_worker = lambda: started.append("poll") or _state()  # type: ignore[assignment]
        assert applet._tick() is True
        assert "poll" in started

        class _Widget:
            def __init__(self, active: bool):
                self._active = active
                self.last_set: bool | None = None

            def get_active(self):
                return self._active

            def set_active(self, value: bool):
                self.last_set = value

        applet._state = _state(paused=True)
        applet._on_toggle_dnd(_Widget(active=True))
        widget = _Widget(active=False)
        applet._backend.set_paused = lambda paused: False  # type: ignore[method-assign]
        applet._on_toggle_dnd(widget)
        assert widget.last_set is True

    def test_poll_worker_refresh_and_activity_expired(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state(available=False))
        monkeypatch.setattr(
            notifications_applet_mod,
            "detect_backend",
            lambda: backend,
        )
        assert applet._poll_worker() == backend.get_state()

        applet._state = _state(available=True, pending_known=False)
        applet._activity_until_monotonic = float("inf")
        applet.present = MagicMock()
        assert applet._on_activity_expired() is False
        applet.present.assert_not_called()

        applet._activity_until_monotonic = 0.0
        assert applet._on_activity_expired() is False
        applet.present.assert_called_once()

    def test_activity_monitor_start_and_stop_paths(self, monkeypatch, caplog):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._activity_monitor_proc = object()  # type: ignore[assignment]
        applet._start_activity_monitor()

        applet._activity_monitor_proc = None
        monkeypatch.setattr(notifications_applet_mod.shutil, "which", lambda cmd: None)
        with caplog.at_level(logging.WARNING, logger="docking.notifications"):
            applet._start_activity_monitor()
        assert "dbus-monitor not found" in caplog.text

        monkeypatch.setattr(
            notifications_applet_mod.shutil,
            "which",
            lambda cmd: "/usr/bin/dbus-monitor",
        )

        def raise_popen(*args, **kwargs):
            raise OSError("missing")

        monkeypatch.setattr(notifications_applet_mod.subprocess, "Popen", raise_popen)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="docking.notifications"):
            applet._start_activity_monitor()
        assert applet._activity_monitor_proc is None
        assert "Failed to start dbus-monitor" in caplog.text

        class _Proc:
            def __init__(self):
                self.stdout = []
                self.killed = False
                self.terminated = False

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=0.0):
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)

            def kill(self):
                self.killed = True

        class _Thread:
            def __init__(self, target, daemon=True):
                self._target = target
                self.started = False

            def start(self):
                self.started = True

        monkeypatch.setattr(notifications_applet_mod.threading, "Thread", _Thread)
        proc = _Proc()
        monkeypatch.setattr(
            notifications_applet_mod.subprocess, "Popen", lambda *a, **k: proc
        )
        applet._start_activity_monitor()
        assert applet._activity_monitor_proc is proc
        applet._stop_activity_monitor()
        assert proc.terminated is True
        assert proc.killed is True

        class _ProcOSError:
            def terminate(self):
                raise OSError("boom")

            def wait(self, timeout=0.0):
                return None

        applet._activity_monitor_proc = _ProcOSError()  # type: ignore[assignment]
        applet._stop_activity_monitor()

    def test_activity_monitor_uses_host_dbus_monitor_in_flatpak(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        monkeypatch.setattr(notifications_applet_mod, "is_flatpak", lambda: True)
        monkeypatch.setattr(
            notifications_applet_mod.shutil,
            "which",
            lambda cmd: "/usr/bin/flatpak-spawn" if cmd == "flatpak-spawn" else None,
        )

        command = applet._activity_monitor_command()

        assert command is not None
        assert command[:3] == ["/usr/bin/flatpak-spawn", "--host", "sh"]
        assert "dbus-monitor --session" in command[-1]
        assert notifications_applet_mod.HOST_MONITOR_PID_PREFIX in command[-1]

    def test_stop_activity_monitor_kills_host_monitor_pid(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._activity_monitor_host_pid = "12345"
        run_calls: list[list[str]] = []

        class _Proc:
            def __init__(self):
                self.terminated = False

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=0.0):
                return None

        proc = _Proc()
        applet._activity_monitor_proc = proc  # type: ignore[assignment]
        monkeypatch.setattr(
            notifications_applet_mod.shutil,
            "which",
            lambda cmd: "/usr/bin/flatpak-spawn" if cmd == "flatpak-spawn" else None,
        )
        monkeypatch.setattr(
            notifications_applet_mod.subprocess,
            "run",
            lambda cmd, **kwargs: run_calls.append(cmd),
        )

        applet._stop_activity_monitor()

        assert run_calls == [["/usr/bin/flatpak-spawn", "--host", "kill", "12345"]]
        assert proc.terminated is True
        assert applet._activity_monitor_host_pid is None

    def test_activity_monitor_worker_and_tooltip_helpers(self, monkeypatch, caplog):
        applet, _backend = _make_applet(monkeypatch, _state())
        idle_calls: list[tuple[object, ...]] = []
        monkeypatch.setattr(
            notifications_applet_mod.GLib,
            "idle_add",
            lambda *args: idle_calls.append(args),
        )

        class _Proc:
            stdout: ClassVar = [
                f"{notifications_applet_mod.HOST_MONITOR_PID_PREFIX}321\n",
                "signal member=Notify\n",
                '   string "Mail"\n',
                '   string "Icon"\n',
                '   string "Subject"\n',
                '   string "Body"\n',
                "   array [\n",
                "signal member=Notify\n",
                '   string "OnlyApp"\n',
                "   int32 1\n",
            ]

        applet._activity_monitor_proc = _Proc()
        applet._activity_monitor_worker()
        assert applet._activity_monitor_host_pid == "321"
        assert any(call[0] == applet._on_notification_event for call in idle_calls)
        assert any(call[0] == applet._on_notification_activity for call in idle_calls)

        class _BrokenStdout:
            def __iter__(self):
                raise RuntimeError("boom")

        class _BrokenProc:
            stdout = _BrokenStdout()

        applet._activity_monitor_proc = _BrokenProc()
        with caplog.at_level(logging.WARNING, logger="docking.notifications"):
            applet._activity_monitor_worker()
        assert "Notification activity monitor stopped unexpectedly" in caplog.text

        applet._history = [
            NotificationEntry(app_name="App", summary="Title", body="Body"),
            NotificationEntry(app_name="", summary="", body=""),
        ]
        applet._history_index = 99
        lines = applet._current_notification_lines()
        assert lines[0].startswith("Notification 2/2:")
        assert notifications_applet_mod.NotificationsApplet._shorten_for_tooltip(
            "   long   spaced   text   ",
            10,
        ).endswith("...")

    def test_history_is_capped(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        monkeypatch.setattr(notifications_applet_mod.time, "monotonic", lambda: 1.0)
        monkeypatch.setattr(
            notifications_applet_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _cb: 1,
        )
        monkeypatch.setattr(
            notifications_applet_mod.GLib, "source_remove", lambda _id: None
        )
        applet.present = MagicMock()
        for i in range(HISTORY_LIMIT + 5):
            applet._on_notification_event("App", f"S{i}", "Body")
        assert len(applet._history) == HISTORY_LIMIT


class TestNotificationsRender:
    def test_icon_renders_sizes(self):
        for size in (32, 48, 64):
            pixbuf = create_notifications_icon(size=size, paused=False, badge_count=4)
            assert pixbuf is not None
            assert pixbuf.get_width() == size
            assert pixbuf.get_height() == size

    def test_icon_renders_activity_dot(self):
        pixbuf = create_notifications_icon(
            size=48,
            paused=False,
            badge_count=0,
            activity=True,
        )
        assert pixbuf is not None
        assert pixbuf.get_width() == 48
