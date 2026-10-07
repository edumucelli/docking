"""Focused tests for the deterministic BDD harness timing behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import docking.ui.autohide as autohide_mod
import docking.ui.dnd as dnd_mod
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


class TestDockHarnessLifecycle:
    def test_start_cleans_up_patchers_when_drag_setup_fails(self, monkeypatch):
        harness = DockHarness()
        original_source_exists = autohide_mod._source_exists
        original_show_poof = dnd_mod.show_poof
        cleanup = MagicMock(wraps=harness.stop)
        monkeypatch.setattr(harness, "stop", cleanup)
        monkeypatch.setattr(
            harness,
            "_build_drag_handler",
            MagicMock(side_effect=RuntimeError("drag setup failed")),
        )

        with pytest.raises(RuntimeError, match="drag setup failed"):
            harness.start()

        cleanup.assert_called_once_with()
        assert harness._patchers == []
        assert autohide_mod._source_exists is original_source_exists
        assert dnd_mod.show_poof is original_show_poof


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


class TestDockHarnessDnDContracts:
    def test_external_launcher_drop_uses_frame_with_cursor_rect(self):
        harness = DockHarness()
        harness.start()
        try:
            harness.drop_external_uri(
                "file:///usr/share/applications/firefox.desktop",
                target_index=0,
            )

            assert harness.external_pinned_targets == ["firefox.desktop"]
            assert harness._dnd_frame.cursor_rect.contains(x=0, y=0)
        finally:
            harness.stop()

    def test_drag_outside_release_uses_window_stack_close_hook(self):
        harness = DockHarness()
        harness.start()
        try:
            harness.drag_outside_and_release("a.desktop")

            assert harness.drag_removed_desktop_id == "a.desktop"
            harness._drag_folder_stack.close.assert_called_once_with()
        finally:
            harness.stop()
