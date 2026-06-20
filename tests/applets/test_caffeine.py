"""Tests for the Caffeine applet."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.applets.caffeine.applet import CaffeineApplet
from docking.applets.caffeine.inhibit import CompositeInhibitor
from docking.applets.caffeine.state import (
    DEFAULT_DURATION,
    INDEFINITE,
    CaffeineState,
    activate,
    deactivate,
    duration_label,
    format_remaining,
    has_timer,
    prefs_from_state,
    set_duration,
    state_from_prefs,
    status_text,
    tick,
    toggle,
    tooltip_text,
)


class FakeInhibitor:
    """In-memory inhibitor recording acquire/release calls."""

    def __init__(self) -> None:
        self.acquired = False
        self.acquire_calls = 0
        self.release_calls = 0

    @property
    def active(self) -> bool:
        return self.acquired

    def acquire(self) -> bool:
        self.acquire_calls += 1
        self.acquired = True
        return True

    def release(self) -> None:
        self.release_calls += 1
        self.acquired = False


class FakePart:
    """Inhibitor part used to exercise CompositeInhibitor fan-out."""

    def __init__(self, succeeds: bool) -> None:
        self._succeeds = succeeds
        self.acquire_calls = 0
        self.release_calls = 0
        self.acquired = False

    @property
    def active(self) -> bool:
        return self.acquired

    def acquire(self) -> bool:
        self.acquire_calls += 1
        self.acquired = self._succeeds
        return self._succeeds

    def release(self) -> None:
        self.release_calls += 1
        self.acquired = False


# -- Pure functions -----------------------------------------------------------


class TestFormatRemaining:
    def test_zero(self):
        assert format_remaining(seconds=0) == "00:00"

    def test_mixed(self):
        assert format_remaining(seconds=90) == "01:30"


class TestDurationLabel:
    def test_indefinite(self):
        assert duration_label(minutes=INDEFINITE) == "Until turned off"

    def test_minutes(self):
        assert duration_label(minutes=30) == "30 min"


class TestTooltipText:
    def test_off(self):
        assert tooltip_text(state=CaffeineState()) == "Caffeine: off"

    def test_indefinite(self):
        state = activate(state=CaffeineState(duration_min=INDEFINITE))
        assert tooltip_text(state=state) == "Caffeine: keeping awake"

    def test_timed_shows_countdown(self):
        state = activate(state=CaffeineState(duration_min=30))
        assert tooltip_text(state=state) == "Caffeine: 30:00 remaining"


class TestStatusText:
    def test_off(self):
        assert status_text(state=CaffeineState()) == "Off"

    def test_indefinite(self):
        state = activate(state=CaffeineState(duration_min=INDEFINITE))
        assert status_text(state=state) == "Active"

    def test_timed_shows_remaining(self):
        state = activate(state=CaffeineState(duration_min=30))
        assert status_text(state=state) == "Active - 30:00 left"


class TestStateTransitions:
    def test_toggle_on_arms_timer_for_finite_duration(self):
        state = toggle(state=CaffeineState(duration_min=15))
        assert state.active is True
        assert state.remaining == 15 * 60
        assert has_timer(state=state) is True

    def test_toggle_on_indefinite_has_no_timer(self):
        state = toggle(state=CaffeineState(duration_min=INDEFINITE))
        assert state.active is True
        assert has_timer(state=state) is False

    def test_toggle_off_clears_remaining(self):
        state = deactivate(state=activate(state=CaffeineState(duration_min=15)))
        assert state.active is False
        assert state.remaining == 0

    def test_set_duration_rearms_while_active(self):
        state = activate(state=CaffeineState(duration_min=15))
        state = set_duration(state=state, minutes=60)
        assert state.remaining == 60 * 60

    def test_set_duration_while_off_does_not_activate(self):
        state = set_duration(state=CaffeineState(), minutes=60)
        assert state.active is False
        assert state.remaining == 0


class TestTick:
    def test_counts_down(self):
        state = activate(state=CaffeineState(duration_min=15))
        state = tick(state=state)
        assert state.remaining == 15 * 60 - 1
        assert state.active is True

    def test_deactivates_at_zero(self):
        state = CaffeineState(active=True, duration_min=1, remaining=1)
        state = tick(state=state)
        assert state.active is False
        assert state.remaining == 0

    def test_indefinite_is_inert(self):
        state = activate(state=CaffeineState(duration_min=INDEFINITE))
        assert tick(state=state) == state


class TestPrefs:
    def test_active_never_persists(self):
        prefs = prefs_from_state(state=activate(state=CaffeineState(duration_min=30)))
        restored = state_from_prefs(prefs=prefs)
        assert restored.active is False
        assert restored.duration_min == 30

    def test_defaults_when_empty(self):
        assert state_from_prefs(prefs=None) == CaffeineState(
            duration_min=DEFAULT_DURATION
        )


# -- CompositeInhibitor -------------------------------------------------------


class TestCompositeInhibitor:
    def test_acquire_runs_all_parts_even_after_failure(self):
        failing = FakePart(succeeds=False)
        working = FakePart(succeeds=True)
        composite = CompositeInhibitor(parts=(failing, working))

        result = composite.acquire()

        assert result is True
        assert failing.acquire_calls == 1
        assert working.acquire_calls == 1

    def test_release_runs_all_parts(self):
        parts = (FakePart(succeeds=True), FakePart(succeeds=True))
        composite = CompositeInhibitor(parts=parts)
        composite.acquire()

        composite.release()

        assert all(part.release_calls == 1 for part in parts)
        assert composite.active is False


# -- Applet -------------------------------------------------------------------


class TestApplet:
    def test_creates_with_icon(self):
        applet = CaffeineApplet(48, inhibitor=FakeInhibitor())
        assert applet.item.icon is not None

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = CaffeineApplet(size, inhibitor=FakeInhibitor())
            pixbuf = applet.create_icon(size=size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_starts_off(self):
        applet = CaffeineApplet(48, inhibitor=FakeInhibitor())
        assert applet._data.active is False
        assert applet.item.name == "Caffeine: off"

    def test_click_toggles_and_acquires(self):
        inhibitor = FakeInhibitor()
        applet = CaffeineApplet(48, inhibitor=inhibitor)

        applet.on_clicked()

        assert applet._data.active is True
        assert inhibitor.active is True
        assert inhibitor.acquire_calls == 1

    def test_second_click_releases(self):
        inhibitor = FakeInhibitor()
        applet = CaffeineApplet(48, inhibitor=inhibitor)

        applet.on_clicked()
        applet.on_clicked()

        assert applet._data.active is False
        assert inhibitor.active is False
        assert inhibitor.release_calls >= 1

    def test_timed_tick_to_zero_releases(self):
        inhibitor = FakeInhibitor()
        applet = CaffeineApplet(48, inhibitor=inhibitor)
        applet._data = CaffeineState(active=True, duration_min=1, remaining=1)
        inhibitor.acquire()

        keep_going = applet._tick()

        assert keep_going is False
        assert applet._data.active is False
        assert inhibitor.active is False

    def test_stop_releases(self):
        inhibitor = FakeInhibitor()
        applet = CaffeineApplet(48, inhibitor=inhibitor)
        applet.on_clicked()

        applet.stop()

        assert inhibitor.active is False

    def test_menu_lists_duration_choices(self):
        applet = CaffeineApplet(48, inhibitor=FakeInhibitor())
        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]
        assert "Until turned off" in labels
        assert "30 min" in labels

    def test_menu_shows_status_header(self):
        applet = CaffeineApplet(48, inhibitor=FakeInhibitor())
        labels = [item.get_label() for item in applet.get_menu_items()]
        assert "Off" in labels

        applet.on_clicked()
        labels = [item.get_label() for item in applet.get_menu_items()]
        assert "Active" in labels


# -- Inhibitors ----------------------------------------------------------------


class TestScreenSaverInhibitor:
    def test_initial_state_inactive(self):
        from docking.applets.caffeine.inhibit import ScreenSaverInhibitor

        inh = ScreenSaverInhibitor()
        assert inh.active is False

    def test_acquire_releases_then_release_is_noop(self):
        from docking.applets.caffeine.inhibit import ScreenSaverInhibitor

        inh = ScreenSaverInhibitor()
        # Release when never acquired is a no-op
        inh.release()
        assert inh.active is False

    def test_release_after_manual_clear_is_noop(self):
        from docking.applets.caffeine.inhibit import ScreenSaverInhibitor

        inh = ScreenSaverInhibitor()
        inh._cookie = 42  # Simulate acquired state
        inh._conn = MagicMock()
        inh._target = ("name", "/path", "iface")
        # Clear cookie manually (simulating release)
        inh._cookie = None
        inh.release()
        assert inh.active is False


class TestSystemdSleepInhibitor:
    def test_initial_state_inactive(self):
        from docking.applets.caffeine.inhibit import SystemdSleepInhibitor

        inh = SystemdSleepInhibitor()
        assert inh.active is False

    def test_release_when_never_acquired_is_noop(self):
        from docking.applets.caffeine.inhibit import SystemdSleepInhibitor

        inh = SystemdSleepInhibitor()
        inh.release()
        assert inh.active is False

    def test_release_when_proc_already_finished_is_noop(self):
        from docking.applets.caffeine.inhibit import SystemdSleepInhibitor

        inh = SystemdSleepInhibitor()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = 0  # Already finished
        inh._proc = fake_proc
        inh.release()
        assert inh.active is False

    def test_release_terminate_oserror_is_handled(self):
        from docking.applets.caffeine.inhibit import SystemdSleepInhibitor

        inh = SystemdSleepInhibitor()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None  # Still running
        fake_proc.terminate.side_effect = OSError("no process")
        inh._proc = fake_proc
        inh.release()
        assert inh._proc is None  # proc cleared

    def test_release_timeout_triggers_kill(self):
        import subprocess

        from docking.applets.caffeine.inhibit import SystemdSleepInhibitor

        inh = SystemdSleepInhibitor()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None  # Still running
        fake_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=2)
        inh._proc = fake_proc
        inh.release()
        fake_proc.kill.assert_called_once()
        assert inh._proc is None


class TestCompositeInhibitorParts:
    def test_default_constructor_creates_parts(self):
        from docking.applets.caffeine.inhibit import CompositeInhibitor

        comp = CompositeInhibitor()
        assert len(comp._parts) == 2  # ScreenSaver + SystemdSleep

    def test_active_property_when_none_active(self):
        from docking.applets.caffeine.inhibit import CompositeInhibitor

        comp = CompositeInhibitor(
            parts=(FakePart(succeeds=False), FakePart(succeeds=False))
        )
        comp.acquire()
        # Both parts failed, neither should be active
        assert all(not p.active for p in comp._parts)

    def test_active_property_when_one_active(self):
        from docking.applets.caffeine.inhibit import CompositeInhibitor

        comp = CompositeInhibitor(
            parts=(FakePart(succeeds=False), FakePart(succeeds=True))
        )
        comp.acquire()
        assert comp.active is True

    def test_default_inhibitor_returns_composite(self):
        from docking.applets.caffeine.inhibit import (
            CompositeInhibitor,
            default_inhibitor,
        )

        inh = default_inhibitor()
        assert isinstance(inh, CompositeInhibitor)
        assert len(inh._parts) == 2


class TestScreenSaverInhibitorDBus:
    def test_acquire_already_active_returns_true(self):
        from docking.applets.caffeine.inhibit import ScreenSaverInhibitor

        inh = ScreenSaverInhibitor()
        inh._cookie = 42
        inh._conn = MagicMock()
        inh._target = ("name", "/path", "iface")
        assert inh.acquire() is True

    def test_acquire_no_session_bus_returns_false(self, monkeypatch):
        from gi.repository import GLib

        from docking.applets.caffeine.inhibit import ScreenSaverInhibitor

        inh = ScreenSaverInhibitor()
        monkeypatch.setattr(
            "docking.applets.caffeine.inhibit.Gio.bus_get_sync",
            lambda *a: (_ for _ in ()).throw(
                GLib.Error(message="no bus", domain="g-io-error-quark", code=0)
            ),
        )
        assert inh.acquire() is False

    def test_acquire_calls_dbus_and_succeeds(self, monkeypatch):
        from docking.applets.caffeine.inhibit import ScreenSaverInhibitor

        inh = ScreenSaverInhibitor()
        fake_conn = MagicMock()
        fake_conn.call_sync.return_value.unpack.return_value = (42,)
        monkeypatch.setattr(
            "docking.applets.caffeine.inhibit.Gio.bus_get_sync",
            lambda *a: fake_conn,
        )
        assert inh.acquire() is True
        assert inh._cookie == 42
        assert inh._conn is fake_conn

    def test_acquire_all_targets_fail_returns_false(self, monkeypatch):
        from gi.repository import GLib

        from docking.applets.caffeine.inhibit import ScreenSaverInhibitor

        inh = ScreenSaverInhibitor()
        fake_conn = MagicMock()
        fake_conn.call_sync.side_effect = GLib.Error(
            message="fail", domain="g-io-error-quark", code=0
        )
        monkeypatch.setattr(
            "docking.applets.caffeine.inhibit.Gio.bus_get_sync",
            lambda *a: fake_conn,
        )
        assert inh.acquire() is False

    def test_release_calls_uninhibit(self, monkeypatch):
        from docking.applets.caffeine.inhibit import ScreenSaverInhibitor

        inh = ScreenSaverInhibitor()
        fake_conn = MagicMock()
        inh._cookie = 42
        inh._conn = fake_conn
        inh._target = (
            "org.freedesktop.ScreenSaver",
            "/ScreenSaver",
            "org.freedesktop.ScreenSaver",
        )
        inh.release()
        fake_conn.call_sync.assert_called_once()
        assert inh._cookie is None

    def test_release_uninhibit_glib_error_is_handled(self, monkeypatch):
        from gi.repository import GLib

        from docking.applets.caffeine.inhibit import ScreenSaverInhibitor

        inh = ScreenSaverInhibitor()
        fake_conn = MagicMock()
        fake_conn.call_sync.side_effect = GLib.Error(
            message="uninhibit fail", domain="g-io-error-quark", code=0
        )
        inh._cookie = 42
        inh._conn = fake_conn
        inh._target = (
            "org.freedesktop.ScreenSaver",
            "/ScreenSaver",
            "org.freedesktop.ScreenSaver",
        )
        # Should not raise
        inh.release()
        assert inh._cookie is None


class TestSystemdSleepInhibitorProc:
    def test_acquire_already_active_returns_true(self):
        from docking.applets.caffeine.inhibit import SystemdSleepInhibitor

        inh = SystemdSleepInhibitor()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None  # Still running
        inh._proc = fake_proc
        assert inh.acquire() is True

    def test_acquire_oserror_returns_false(self, monkeypatch):
        import subprocess

        from docking.applets.caffeine.inhibit import SystemdSleepInhibitor

        inh = SystemdSleepInhibitor()
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("no systemd-inhibit")),
        )
        assert inh.acquire() is False
        assert inh._proc is None
