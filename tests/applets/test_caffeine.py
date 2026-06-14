"""Tests for the Caffeine applet."""

from __future__ import annotations

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
