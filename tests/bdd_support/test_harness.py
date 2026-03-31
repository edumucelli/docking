"""Focused tests for the deterministic BDD harness timing behavior."""

from __future__ import annotations

import docking.ui.hover as hover_mod
from tests.bdd_support.harness import DockHarness, _TimerScheduler


class TestTimerScheduler:
    def test_advance_only_runs_callbacks_after_requested_delay(self):
        scheduler = _TimerScheduler()
        fired: list[str] = []

        scheduler.timeout_add(10, lambda: fired.append("ten") or False)
        scheduler.timeout_add(25, lambda: fired.append("twenty-five") or False)

        scheduler.advance(9)
        assert fired == []

        scheduler.advance(1)
        assert fired == ["ten"]

        scheduler.advance(14)
        assert fired == ["ten"]

        scheduler.advance(1)
        assert fired == ["ten", "twenty-five"]

    def test_repeating_callbacks_reschedule_by_interval(self):
        scheduler = _TimerScheduler()
        fired: list[int] = []

        def tick() -> bool:
            fired.append(len(fired) + 1)
            return len(fired) < 3

        scheduler.timeout_add(16, tick)

        scheduler.advance(15)
        assert fired == []

        scheduler.advance(1)
        assert fired == [1]

        scheduler.advance(16)
        assert fired == [1, 2]

        scheduler.advance(16)
        assert fired == [1, 2, 3]


class TestDockHarnessTiming:
    def test_hover_running_item_requires_full_preview_delay(self):
        harness = DockHarness()
        harness.start()
        try:
            item = harness._hover_item_by_desktop_id("firefox.desktop")
            harness._hover_frame.hover_item_at_point.return_value = item
            harness._hover_window.cursor_x = 20.0
            harness._hover_window.cursor_y = 10.0
            harness._hover_window.dock_hovered = True

            harness._hover_manager.update(cursor_main=20.0)
            assert harness.preview_visible is False

            harness.advance_time(hover_mod.PREVIEW_SHOW_DELAY_MS - 1)
            assert harness.preview_visible is False

            harness.advance_time(1)
            assert harness.preview_visible is True
        finally:
            harness.stop()
