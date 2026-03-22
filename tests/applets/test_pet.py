"""Tests for pet applet — state, render, and lifecycle."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.applets.pet.state import (
    HYSTERESIS,
    SLEEPING_TICKS,
    SLEEPY_TICKS,
    Mood,
    PetState,
    mood_color,
    mood_label,
    reset_to_happy,
    resolve_mood,
    tick,
    tooltip_text,
)

# ---------------------------------------------------------------------------
# resolve_mood
# ---------------------------------------------------------------------------


class TestResolveMood:
    def test_happy_at_moderate_cpu(self):
        # Given moderate CPU, no idle
        # When
        result = resolve_mood(cpu=0.30, prev_cpu=0.25, idle_ticks=0)
        # Then
        assert result == Mood.HAPPY

    def test_relaxed_at_low_cpu(self):
        # Given low but non-idle CPU
        result = resolve_mood(cpu=0.10, prev_cpu=0.10, idle_ticks=0)
        assert result == Mood.RELAXED

    def test_focused_at_moderate_cpu(self):
        # Given 40-60% CPU
        result = resolve_mood(cpu=0.50, prev_cpu=0.45, idle_ticks=0)
        assert result == Mood.FOCUSED

    def test_busy_at_high_cpu(self):
        # Given 60-80% CPU
        result = resolve_mood(cpu=0.70, prev_cpu=0.65, idle_ticks=0)
        assert result == Mood.BUSY

    def test_stressed_at_very_high_cpu(self):
        # Given 80-90% CPU
        result = resolve_mood(cpu=0.85, prev_cpu=0.80, idle_ticks=0)
        assert result == Mood.STRESSED

    def test_excited_at_very_high_cpu(self):
        # Given very high CPU
        result = resolve_mood(cpu=0.95, prev_cpu=0.90, idle_ticks=0)
        assert result == Mood.EXCITED

    def test_excited_on_spike(self):
        # Given large CPU spike
        result = resolve_mood(cpu=0.50, prev_cpu=0.15, idle_ticks=0)
        assert result == Mood.EXCITED

    def test_sleepy_after_idle_threshold(self):
        # Given CPU below idle threshold for long enough
        result = resolve_mood(cpu=0.03, prev_cpu=0.03, idle_ticks=SLEEPY_TICKS)
        assert result == Mood.SLEEPY

    def test_sleeping_after_long_idle(self):
        # Given CPU below idle threshold for very long
        result = resolve_mood(cpu=0.03, prev_cpu=0.03, idle_ticks=SLEEPING_TICKS)
        assert result == Mood.SLEEPING

    def test_relaxed_when_idle_ticks_but_cpu_above_threshold(self):
        # Given CPU above idle threshold even with high idle_ticks
        result = resolve_mood(cpu=0.15, prev_cpu=0.15, idle_ticks=SLEEPING_TICKS)
        assert result == Mood.RELAXED

    def test_drowsy_after_one_minute_idle(self):
        from docking.applets.pet.state import DROWSY_TICKS

        result = resolve_mood(cpu=0.03, prev_cpu=0.03, idle_ticks=DROWSY_TICKS)
        assert result == Mood.DROWSY

    def test_sleepy_not_sleeping_below_sleeping_ticks(self):
        # Given idle ticks between sleepy and sleeping
        result = resolve_mood(
            cpu=0.03,
            prev_cpu=0.03,
            idle_ticks=SLEEPY_TICKS + 10,
        )
        assert result == Mood.SLEEPY


# ---------------------------------------------------------------------------
# tick
# ---------------------------------------------------------------------------


class TestTick:
    def test_first_tick_stays_happy(self):
        # Given fresh state
        state = PetState()
        # When
        result = tick(state=state, raw_cpu=0.20)
        # Then
        assert result.state.mood == Mood.HAPPY
        assert not result.mood_changed

    def test_smoothing_applies(self):
        # Given previous smoothed CPU of 0.0
        state = PetState(smoothed_cpu=0.0)
        # When
        result = tick(state=state, raw_cpu=1.0)
        # Then — smoothed should be between 0 and 1
        assert 0 < result.state.smoothed_cpu < 1.0

    def test_idle_ticks_increment_when_cpu_low(self):
        # Given low CPU
        state = PetState(smoothed_cpu=0.02, idle_ticks=5)
        # When
        result = tick(state=state, raw_cpu=0.02)
        # Then
        assert result.state.idle_ticks > 5

    def test_idle_ticks_reset_when_cpu_rises(self):
        # Given accumulated idle ticks, then high CPU
        state = PetState(smoothed_cpu=0.50, idle_ticks=100)
        # When
        result = tick(state=state, raw_cpu=0.80)
        # Then
        assert result.state.idle_ticks == 0

    def test_hysteresis_prevents_immediate_mood_change(self):
        # Given relaxed state, one tick of busy-range CPU
        state = PetState(mood=Mood.RELAXED, smoothed_cpu=0.65)
        # When — single tick at busy CPU
        result = tick(state=state, raw_cpu=0.75)
        # Then — mood should NOT change yet
        assert result.state.mood == Mood.RELAXED
        assert not result.mood_changed
        assert result.state.pending_mood == Mood.BUSY

    def test_hysteresis_commits_after_enough_ticks(self):
        # Given relaxed state, sustained busy CPU
        state = PetState(mood=Mood.RELAXED, smoothed_cpu=0.65)
        for _ in range(HYSTERESIS):
            result = tick(state=state, raw_cpu=0.75)
            state = result.state
        # Then — mood should have changed
        assert state.mood == Mood.BUSY
        assert result.mood_changed

    def test_hysteresis_resets_on_fluctuation(self):
        # Given pending busy mood from relaxed
        state = PetState(
            mood=Mood.RELAXED,
            smoothed_cpu=0.15,
            pending_mood=Mood.BUSY,
            pending_count=HYSTERESIS - 1,
        )
        # When — CPU stays in relaxed range (smoothed ~0.12)
        result = tick(state=state, raw_cpu=0.10)
        # Then — pending busy should reset (target is RELAXED, matches mood)
        assert result.state.pending_mood is None
        assert result.state.pending_count == 0

    def test_should_refresh_on_cpu_change(self):
        # Given a noticeable CPU jump
        state = PetState(cpu=0.10, smoothed_cpu=0.10)
        # When
        result = tick(state=state, raw_cpu=0.50)
        # Then
        assert result.should_refresh

    def test_no_refresh_on_tiny_cpu_change(self):
        # Given tiny CPU change, same mood
        state = PetState(cpu=0.20, smoothed_cpu=0.20)
        # When
        result = tick(state=state, raw_cpu=0.21)
        # Then
        assert not result.should_refresh


# ---------------------------------------------------------------------------
# reset_to_happy
# ---------------------------------------------------------------------------


class TestResetToHappy:
    def test_resets_mood(self):
        # Given sleeping state
        state = PetState(mood=Mood.SLEEPING, pending_mood=Mood.BUSY, pending_count=2)
        # When
        result = reset_to_happy(state=state)
        # Then
        assert result.mood == Mood.HAPPY
        assert result.pending_mood is None
        assert result.pending_count == 0

    def test_preserves_cpu(self):
        # Given state with CPU readings
        state = PetState(mood=Mood.BUSY, cpu=0.75, smoothed_cpu=0.75)
        # When
        result = reset_to_happy(state=state)
        # Then
        assert result.cpu == 0.75


# ---------------------------------------------------------------------------
# mood_color / mood_label / tooltip_text
# ---------------------------------------------------------------------------


class TestMoodHelpers:
    @pytest.mark.parametrize("mood", list(Mood))
    def test_mood_color_returns_rgb_tuple(self, mood):
        red, green, blue = mood_color(mood=mood)
        assert 0 <= red <= 1
        assert 0 <= green <= 1
        assert 0 <= blue <= 1

    @pytest.mark.parametrize("mood", list(Mood))
    def test_mood_label_nonempty(self, mood):
        assert len(mood_label(mood=mood)) > 0

    def test_tooltip_text_contains_cpu(self):
        result = tooltip_text(mood=Mood.HAPPY, cpu=0.234)
        assert "23" in result

    def test_tooltip_text_contains_mood(self):
        result = tooltip_text(mood=Mood.EXCITED, cpu=0.90)
        assert "Excited" in result


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


class TestRender:
    @pytest.mark.parametrize("mood", list(Mood))
    def test_render_icon_returns_pixbuf(self, mood):
        from docking.applets.pet.render import render_icon

        state = PetState(mood=mood, cpu=0.50)
        result = render_icon(size=48, state=state)
        assert result is not None

    @pytest.mark.parametrize("size", [32, 48, 64])
    def test_render_at_various_sizes(self, size):
        from docking.applets.pet.render import render_icon

        state = PetState()
        result = render_icon(size=size, state=state)
        assert result is not None
        assert result.get_width() == size
        assert result.get_height() == size


# ---------------------------------------------------------------------------
# applet lifecycle
# ---------------------------------------------------------------------------


class TestPetApplet:
    def test_creates_with_happy_mood(self):
        from docking.applets.pet.applet import PetApplet

        applet = PetApplet(icon_size=48)
        assert applet._state.mood == Mood.HAPPY

    def test_create_icon_returns_pixbuf(self):
        from docking.applets.pet.applet import PetApplet

        applet = PetApplet(icon_size=48)
        result = applet.create_icon(size=48)
        assert result is not None

    def test_refresh_tooltip_sets_name(self):
        from docking.applets.pet.applet import PetApplet

        applet = PetApplet(icon_size=48)
        applet.refresh_tooltip()
        assert applet.item.name
        assert "Happy" in applet.item.name

    def test_on_clicked_resets_to_happy(self):
        from docking.applets.pet.applet import PetApplet

        applet = PetApplet(icon_size=48)
        applet._state = PetState(mood=Mood.SLEEPING)
        applet.item.is_urgent = True
        applet.on_clicked()
        assert applet._state.mood == Mood.HAPPY
        assert not applet.item.is_urgent

    def test_start_registers_timer(self, monkeypatch):
        import docking.applets.pet.applet as pet_mod
        from docking.applets.pet.applet import PetApplet

        timer_ids = iter([42])
        monkeypatch.setattr(
            pet_mod.GLib,
            "timeout_add_seconds",
            lambda _s, _cb: next(timer_ids),
        )
        applet = PetApplet(icon_size=48)
        applet.start(notify=MagicMock())
        assert applet._timer_id == 42

    def test_stop_removes_timer(self, monkeypatch):
        import docking.applets.pet.applet as pet_mod
        from docking.applets.pet.applet import PetApplet

        removed = []
        monkeypatch.setattr(
            pet_mod.GLib,
            "timeout_add_seconds",
            lambda _s, _cb: 42,
        )
        monkeypatch.setattr(
            pet_mod.GLib,
            "source_remove",
            lambda sid: removed.append(sid),
        )
        applet = PetApplet(icon_size=48)
        applet.start(notify=MagicMock())
        applet.stop()
        assert removed == [42]
        assert applet._timer_id == 0

    def test_tick_reads_proc_stat(self):
        from docking.applets.pet.applet import PetApplet

        proc_data = "cpu  100 0 50 800 10 5 3\n"
        applet = PetApplet(icon_size=48)
        # First tick just records the sample
        with patch("builtins.open", mock_open(read_data=proc_data)):
            applet._tick()
        assert applet._prev_sample is not None
        # Second tick computes CPU
        with patch("builtins.open", mock_open(read_data=proc_data)):
            applet._tick()

    def test_tick_handles_proc_stat_error(self):
        from docking.applets.pet.applet import PetApplet

        applet = PetApplet(icon_size=48)
        with patch("builtins.open", side_effect=OSError("nope")):
            result = applet._tick()
        assert result is True

    def test_excited_mood_sets_urgent(self, monkeypatch):
        import docking.applets.pet.applet as pet_mod
        from docking.applets.pet.applet import PetApplet

        monkeypatch.setattr(pet_mod.GLib, "get_monotonic_time", lambda: 999)
        applet = PetApplet(icon_size=48)
        # Force state one tick away from committing EXCITED
        applet._state = PetState(
            mood=Mood.HAPPY,
            cpu=0.95,
            smoothed_cpu=0.95,
            pending_mood=Mood.EXCITED,
            pending_count=HYSTERESIS - 1,
        )
        from docking.applets.systemmonitor.state import CpuSample

        # prev_sample: 100% busy (0 idle out of 1000 total)
        applet._prev_sample = CpuSample(total=1000, idle=0)
        # curr_sample: still 100% busy (2618 total, 0 idle)
        proc_data = "cpu  2000 0 500 0 10 5 3\n"
        with patch.object(
            type(pet_mod._PROC_STAT), "open", mock_open(read_data=proc_data)
        ):
            applet._tick()
        assert applet.item.is_urgent is True
