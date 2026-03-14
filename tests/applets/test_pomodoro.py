"""Tests for the Pomodoro applet."""

from unittest.mock import MagicMock

import docking.applets.pomodoro.applet as pomodoro_mod
from docking.applets.pomodoro import (
    DEFAULT_BREAK,
    DEFAULT_LONG_BREAK,
    DEFAULT_WORK,
    LONG_BREAK_EVERY,
    PomodoroApplet,
    State,
    format_time,
    tooltip_text,
)

# -- Pure functions -----------------------------------------------------------


class TestFormatTime:
    def test_zero(self):
        assert format_time(seconds=0) == "00:00"

    def test_one_minute(self):
        assert format_time(seconds=60) == "01:00"

    def test_twenty_five_minutes(self):
        assert format_time(seconds=25 * 60) == "25:00"

    def test_mixed(self):
        assert format_time(seconds=5 * 60 + 37) == "05:37"


class TestTooltipText:
    def test_idle(self):
        assert tooltip_text(state=State.IDLE, remaining=0) == "Pomodoro"

    def test_work(self):
        assert tooltip_text(state=State.WORK, remaining=1500) == "Work: 25:00 remaining"

    def test_break(self):
        assert (
            tooltip_text(state=State.BREAK, remaining=300) == "Break: 05:00 remaining"
        )

    def test_long_break(self):
        result = tooltip_text(state=State.LONG_BREAK, remaining=900)
        assert result == "Long Break: 15:00 remaining"

    def test_paused(self):
        assert tooltip_text(state=State.PAUSED, remaining=600) == "Paused - 10:00"


# -- State machine ------------------------------------------------------------


class TestStateMachine:
    def test_starts_idle(self):
        applet = PomodoroApplet(48)
        assert applet._state == State.IDLE
        assert applet._remaining == 0

    def test_click_starts_work(self):
        applet = PomodoroApplet(48)
        applet.on_clicked()
        assert applet._state == State.WORK
        assert applet._remaining == DEFAULT_WORK * 60

    def test_click_pauses_work(self):
        applet = PomodoroApplet(48)
        applet.on_clicked()  # idle → work
        applet.on_clicked()  # work → paused
        assert applet._state == State.PAUSED
        assert applet._paused_from == State.WORK

    def test_click_resumes_from_pause(self):
        applet = PomodoroApplet(48)
        applet.on_clicked()  # idle → work
        remaining = applet._remaining
        applet.on_clicked()  # work → paused
        applet.on_clicked()  # paused → work
        assert applet._state == State.WORK
        assert applet._remaining == remaining

    def test_work_transitions_to_break(self):
        applet = PomodoroApplet(48)
        applet.on_clicked()  # idle → work
        applet._remaining = 1
        applet._tick()  # remaining → 0 → auto-transition
        assert applet._state == State.BREAK
        assert applet._remaining == DEFAULT_BREAK * 60

    def test_auto_transition_triggers_urgent(self):
        applet = PomodoroApplet(48)
        applet.on_clicked()  # idle → work
        applet._remaining = 1
        applet._tick()  # triggers auto-transition
        assert applet.item.is_urgent is True
        assert applet.item.last_urgent > 0

    def test_break_transitions_to_work(self):
        applet = PomodoroApplet(48)
        applet._state = State.BREAK
        applet._remaining = 1
        applet._tick()
        assert applet._state == State.WORK
        assert applet._remaining == DEFAULT_WORK * 60

    def test_long_break_every_n_cycles(self):
        applet = PomodoroApplet(48)
        # Simulate completing LONG_BREAK_EVERY work sessions
        for i in range(LONG_BREAK_EVERY):
            applet._state = State.WORK
            applet._remaining = 1
            applet._tick()  # triggers auto-transition
            if i < LONG_BREAK_EVERY - 1:
                assert applet._state == State.BREAK
                # Transition break → work for next cycle
                applet._state = State.BREAK
                applet._remaining = 1
                applet._tick()
        assert applet._state == State.LONG_BREAK
        assert applet._remaining == DEFAULT_LONG_BREAK * 60

    def test_long_break_transitions_to_work(self):
        applet = PomodoroApplet(48)
        applet._state = State.LONG_BREAK
        applet._remaining = 1
        applet._tick()
        assert applet._state == State.WORK

    def test_tick_noop_when_idle(self):
        applet = PomodoroApplet(48)
        assert applet._tick() is True
        assert applet._state == State.IDLE

    def test_tick_noop_when_paused(self):
        applet = PomodoroApplet(48)
        applet.on_clicked()  # idle → work
        remaining = applet._remaining
        applet.on_clicked()  # work → paused
        applet._tick()
        assert applet._remaining == remaining


class TestReset:
    def test_reset_from_work(self):
        applet = PomodoroApplet(48)
        applet.on_clicked()  # idle → work
        applet._reset()
        assert applet._state == State.IDLE
        assert applet._remaining == 0
        assert applet._work_count == 0


# -- Icon rendering -----------------------------------------------------------


class TestCreateIcon:
    def test_renders_at_various_sizes(self):
        applet = PomodoroApplet(48)
        for size in [32, 48, 64]:
            pixbuf = applet.create_icon(size=size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size
            assert pixbuf.get_height() == size

    def test_renders_in_work_state(self):
        applet = PomodoroApplet(48)
        applet.on_clicked()
        pixbuf = applet.create_icon(size=48)
        assert pixbuf is not None

    def test_renders_in_paused_state(self):
        applet = PomodoroApplet(48)
        applet.on_clicked()
        applet.on_clicked()  # paused
        pixbuf = applet.create_icon(size=48)
        assert pixbuf is not None

    def test_renders_in_break_state(self):
        applet = PomodoroApplet(48)
        applet._state = State.BREAK
        applet._remaining = 300
        pixbuf = applet.create_icon(size=48)
        assert pixbuf is not None


# -- Menu ---------------------------------------------------------------------


class TestMenu:
    def test_has_reset_item(self):
        applet = PomodoroApplet(48)
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items if mi.get_label()]
        assert "Reset" in labels

    def test_has_work_presets(self):
        applet = PomodoroApplet(48)
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items if mi.get_label()]
        assert "25 min" in labels
        assert "45 min" in labels

    def test_has_break_presets(self):
        applet = PomodoroApplet(48)
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items if mi.get_label()]
        assert "5 min" in labels
        assert "10 min" in labels


# -- Tooltip ------------------------------------------------------------------


class TestTooltip:
    def test_idle_tooltip(self):
        applet = PomodoroApplet(48)
        assert applet.item.name == "Pomodoro"

    def test_work_tooltip(self):
        applet = PomodoroApplet(48)
        applet.on_clicked()
        assert "Work" in applet.item.name
        assert "remaining" in applet.item.name

    def test_paused_tooltip(self):
        applet = PomodoroApplet(48)
        applet.on_clicked()
        applet.on_clicked()
        assert "Paused" in applet.item.name


class TestPomodoroInternals:
    def test_property_setters_cover_internal_mutators(self):
        applet = PomodoroApplet(48)
        applet._paused_from = State.WORK
        applet._work_count = 2
        applet._work_min = 30
        applet._break_min = 10
        applet._long_break_min = 20
        applet._show_timer = False

        assert applet._paused_from == State.WORK
        assert applet._work_count == 2
        assert applet._work_min == 30
        assert applet._break_min == 10
        assert applet._long_break_min == 20
        assert applet._show_timer is False

    def test_start_and_stop_manage_timer(self, monkeypatch):
        applet = PomodoroApplet(48)
        monkeypatch.setattr(
            pomodoro_mod.GLib, "timeout_add_seconds", lambda _s, _cb: 515
        )
        removed = []
        monkeypatch.setattr(
            pomodoro_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.start(lambda: None)
        assert applet._timer_id == 515

        applet.stop()
        assert removed == [515]
        assert applet._timer_id == 0

    def test_toggle_and_duration_setters_save(self):
        applet = PomodoroApplet(48)
        applet._save = MagicMock()
        applet.present = MagicMock()

        widget = MagicMock()
        widget.get_active.return_value = False
        applet._on_toggle_timer(widget)
        applet._set_work(35)
        applet._set_break(7)
        applet._set_long_break(18)

        assert applet._show_timer is False
        assert applet._work_min == 35
        assert applet._break_min == 7
        assert applet._long_break_min == 18
        assert applet._save.call_count == 4
        applet.present.assert_called_once()

    def test_make_duration_header_and_radio_item(self):
        header = PomodoroApplet._make_duration_header("Work")
        assert header.get_label() == "Work"
        assert header.get_sensitive() is False

        callback = MagicMock()
        radio = PomodoroApplet._make_radio_item("25 min", True, callback)
        assert radio.get_active() is True
