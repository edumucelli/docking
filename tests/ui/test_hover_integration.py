"""Integration-style tests for HoverManager."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.hover as hover_mod
from docking.core.position import Position
from docking.platform.model import DockItem
from docking.ui.autohide import HideState
from docking.ui.interaction import DockInteractionCoordinator


def _make_hover():
    window = MagicMock()
    window.get_realized.return_value = True
    window.get_position.return_value = (100, 200)
    window.get_size.return_value = (500, 60)
    window.dock_hovered = True
    window.drawing_area = MagicMock()
    window.cursor_x = 20.0
    window.cursor_y = 10.0
    frame = SimpleNamespace(
        item_at_point=MagicMock(return_value=None),
        hover_item_at_point=MagicMock(return_value=None),
        geometry_for_item=MagicMock(
            return_value=SimpleNamespace(
                draw_rect=SimpleNamespace(x=15, y=4, w=48, h=48)
            )
        ),
        cursor_rect=SimpleNamespace(contains=lambda *_args, **_kwargs: True),
    )
    model = MagicMock()
    config = SimpleNamespace(
        previews_enabled=True,
        icon_size=48,
        pos=Position.BOTTOM,
    )
    theme = SimpleNamespace(item_padding=8, horizontal_padding=10, bottom_padding=12)
    tooltip = MagicMock()
    hover = hover_mod.HoverManager(
        window,
        config,
        model,
        theme,
        tooltip,
        geometry_builder=SimpleNamespace(build_frame=lambda **_kwargs: frame),
    )
    return hover, window, model, config, tooltip, frame


def _layout():
    return [SimpleNamespace(x=0.0, scale=1.0, width=48.0)]


class TestHoverUpdates:
    def test_update_changes_hover_and_starts_preview_timer(self, monkeypatch):
        # Given
        hover, _window, model, _config, tooltip, frame = _make_hover()
        item = DockItem(
            desktop_id="firefox.desktop",
            name="Firefox",
            is_running=True,
            instance_count=1,
        )
        model.visible_items.return_value = [item]
        frame.hover_item_at_point.return_value = item
        hover.set_preview(MagicMock())
        monkeypatch.setattr(hover_mod.GLib, "timeout_add", lambda _ms, _cb, *_args: 77)

        # When
        hover.update(cursor_main=20.0)
        # Then
        assert hover.hovered_item is item
        tooltip.update.assert_called_once_with(item, frame)
        assert hover._preview_timer_id == 77

    def test_update_same_item_only_refreshes_tooltip(self):
        # Given
        hover, _window, model, _config, tooltip, frame = _make_hover()
        item = DockItem(desktop_id="x.desktop", name="X")
        hover.hovered_item = item
        model.visible_items.return_value = [item]
        frame.hover_item_at_point.return_value = item

        hover.cancel = MagicMock()
        # When
        hover.update(cursor_main=20.0)
        # Then
        tooltip.update.assert_called_once_with(item, frame)
        hover.cancel.assert_not_called()

    def test_update_non_running_item_schedules_hide(self):
        # Given
        hover, _window, model, config, _tooltip, frame = _make_hover()
        item = DockItem(
            desktop_id="x.desktop", name="X", is_running=False, instance_count=0
        )
        model.visible_items.return_value = [item]
        frame.hover_item_at_point.return_value = item
        preview = MagicMock()
        hover.set_preview(preview)
        config.previews_enabled = True

        # When
        hover.update(cursor_main=20.0)
        # Then
        preview.schedule_hide.assert_called_once()

    def test_update_does_not_re_show_tooltip_when_dock_is_not_effectively_hovered(self):
        hover, window, model, _config, tooltip, frame = _make_hover()
        item = DockItem(desktop_id="terminator.desktop", name="Terminator")
        hover.hovered_item = item
        window.dock_hovered = False
        model.visible_items.return_value = [item]
        frame.hover_item_at_point.return_value = item

        hover.update(cursor_main=20.0)

        tooltip.hide.assert_called_once()
        tooltip.update.assert_not_called()
        assert hover.hovered_item is item

    def test_update_suppresses_tooltip_while_dock_is_showing(self):
        hover, window, model, _config, tooltip, frame = _make_hover()
        item = DockItem(desktop_id="terminator.desktop", name="Terminator")
        window.autohide = SimpleNamespace(enabled=True, state=HideState.SHOWING)
        model.visible_items.return_value = [item]
        frame.hover_item_at_point.return_value = item

        hover.update(cursor_main=20.0)

        assert hover.hovered_item is item
        tooltip.hide.assert_called_once()
        tooltip.update.assert_not_called()


class TestPreviewReentry:
    @pytest.fixture
    def retained_hover(self, monkeypatch):
        hover, window, _model, config, _tooltip, frame = _make_hover()
        item = DockItem(
            desktop_id="firefox.desktop",
            name="Firefox",
            is_running=True,
            instance_count=1,
        )
        frame.hover_item_at_point.return_value = item
        hover.hovered_item = item
        preview = MagicMock()
        preview.get_visible.return_value = False
        preview.current_desktop_id = item.desktop_id
        hover.set_preview(preview)
        window.hover = hover
        window.preview = preview
        timer = MagicMock(return_value=77)
        monkeypatch.setattr(hover_mod.GLib, "timeout_add", timer)
        monkeypatch.setattr(hover_mod.GLib, "source_remove", MagicMock())
        return hover, window, config, frame, item, preview, timer

    @pytest.mark.parametrize("autohide", [False, True])
    def test_same_icon_rearms_after_preview_dismissal(self, retained_hover, autohide):
        hover, window, _config, frame, item, preview, timer = retained_hover
        window.autohide.enabled = autohide
        preview.get_visible.return_value = True
        interaction = DockInteractionCoordinator(window)
        interaction.on_effective_leave(window.drawing_area)
        assert hover.hovered_item is item  # Kept for the handoff/hide animation.
        preview.get_visible.return_value = False

        interaction.on_effective_enter()
        hover.update(20, frame)

        timer.assert_called_once_with(
            hover_mod.PREVIEW_SHOW_DELAY_MS, hover._show_preview, item, frame
        )
        assert hover._preview_timer_id == 77

    def test_motion_does_not_restart_reentry_deadline(self, retained_hover):
        hover, _window, _config, frame, _item, _preview, timer = retained_hover
        for _ in range(10):
            hover.update(20, frame)
        timer.assert_called_once()
        assert hover._preview_timer_id == 77

    def test_return_to_visible_preview_cancels_hide_without_rebuilding(
        self, retained_hover
    ):
        hover, _window, _config, frame, _item, preview, timer = retained_hover
        preview.get_visible.return_value = True

        hover.update(20, frame)

        preview.cancel_hide.assert_called_once()
        preview.show_for_item.assert_not_called()
        timer.assert_not_called()

    def test_other_apps_visible_preview_does_not_suppress_rearming(
        self, retained_hover
    ):
        hover, _window, _config, frame, _item, preview, timer = retained_hover
        preview.get_visible.return_value = True
        preview.current_desktop_id = "other.desktop"
        hover.update(20, frame)
        timer.assert_called_once()
        preview.cancel_hide.assert_not_called()

    @pytest.mark.parametrize("reason", ["disabled", "stopped", "no-windows"])
    def test_ineligible_retained_hover_cancels_pending_preview(
        self, retained_hover, reason
    ):
        hover, _window, config, frame, item, _preview, timer = retained_hover
        hover._preview_timer_id = 55
        if reason == "disabled":
            config.previews_enabled = False
        elif reason == "stopped":
            item.is_running = False
        else:
            item.instance_count = 0
        hover.update(20, frame)
        assert hover._preview_timer_id == 0
        hover_mod.GLib.source_remove.assert_called_once_with(55)
        timer.assert_not_called()

    @pytest.mark.parametrize(
        "reason", ["off-dock", "disabled", "stopped", "no-windows"]
    )
    def test_delayed_callback_rechecks_eligibility(self, retained_hover, reason):
        hover, window, config, _frame, item, preview, _timer = retained_hover
        if reason == "off-dock":
            window.dock_hovered = False
        elif reason == "disabled":
            config.previews_enabled = False
        elif reason == "stopped":
            item.is_running = False
        else:
            item.instance_count = 0
        assert hover._show_preview(item, object()) is False
        preview.show_for_item.assert_not_called()


class TestHoverTimers:
    def test_cancel_removes_preview_timer(self, monkeypatch):
        # Given
        hover, _window, _model, _config, _tooltip, _frame = _make_hover()
        hover._preview_timer_id = 9
        removed = []
        monkeypatch.setattr(
            hover_mod.GLib, "source_remove", lambda sid: removed.append(sid)
        )

        # When
        hover.cancel()
        # Then
        assert removed == [9]
        assert hover._preview_timer_id == 0

    def test_start_anim_pump_ticks_and_stops(self, monkeypatch):
        # Given
        hover, window, _model, _config, _tooltip, _frame = _make_hover()
        callbacks = []
        monkeypatch.setattr(
            hover_mod.GLib,
            "timeout_add",
            lambda _ms, cb: callbacks.append(cb) or 1,
        )

        # When
        hover.start_anim_pump(duration_ms=48)
        # Then
        assert callbacks
        tick = callbacks[0]
        assert tick() is True
        assert tick() is True
        assert tick() is False
        window.drawing_area.queue_draw.assert_called()
        assert hover._anim_timer_id == 0

    def test_start_anim_pump_cancels_previous_timer(self, monkeypatch):
        # Given
        hover, _window, _model, _config, _tooltip, _frame = _make_hover()
        removed = []
        monkeypatch.setattr(
            hover_mod.GLib, "source_remove", lambda sid: removed.append(sid)
        )
        timer_ids = iter([10, 20])
        monkeypatch.setattr(
            hover_mod.GLib,
            "timeout_add",
            lambda _ms, _cb: next(timer_ids),
        )

        # When - first pump sets timer id 10
        hover.start_anim_pump(duration_ms=48)
        assert hover._anim_timer_id == 10
        # When - second pump cancels timer 10
        hover.start_anim_pump(duration_ms=48)

        # Then
        assert removed == [10]
        assert hover._anim_timer_id == 20

    def test_start_anim_pump_zero_duration_no_timer(self, monkeypatch):
        # Given
        hover, _window, _model, _config, _tooltip, _frame = _make_hover()
        callbacks = []
        monkeypatch.setattr(
            hover_mod.GLib,
            "timeout_add",
            lambda _ms, cb: callbacks.append(cb) or 1,
        )

        # When
        hover.start_anim_pump(duration_ms=0)

        # Then - timer fires once and immediately stops
        assert callbacks
        tick = callbacks[0]
        assert tick() is False
        assert hover._anim_timer_id == 0

    def test_start_anim_pump_queue_draw_called_each_tick(self, monkeypatch):
        # Given
        hover, window, _model, _config, _tooltip, _frame = _make_hover()
        callbacks = []
        monkeypatch.setattr(
            hover_mod.GLib,
            "timeout_add",
            lambda _ms, cb: callbacks.append(cb) or 1,
        )
        hover.start_anim_pump(duration_ms=32)
        tick = callbacks[0]

        # When
        window.drawing_area.queue_draw.reset_mock()
        tick()

        # Then - each live tick triggers a redraw
        window.drawing_area.queue_draw.assert_called_once()

    def test_start_anim_pump_from_idle_no_source_remove(self, monkeypatch):
        # Given - no prior timer running
        hover, _window, _model, _config, _tooltip, _frame = _make_hover()
        assert hover._anim_timer_id == 0
        removed = []
        monkeypatch.setattr(
            hover_mod.GLib, "source_remove", lambda sid: removed.append(sid)
        )
        monkeypatch.setattr(
            hover_mod.GLib,
            "timeout_add",
            lambda _ms, _cb: 1,
        )

        # When
        hover.start_anim_pump(duration_ms=48)

        # Then - no source_remove called when there was no active timer
        assert removed == []

    def test_on_model_changed_starts_pump_for_urgent_item(self):
        # Given
        hover, _window, model, _config, _tooltip, _frame = _make_hover()
        urgent = DockItem(desktop_id="u.desktop", is_urgent=True, last_urgent=123)
        model.visible_items.return_value = [urgent]
        hover.start_anim_pump = MagicMock()

        # When
        hover.on_model_changed()
        # Then
        hover.start_anim_pump.assert_called_once_with(duration_ms=700)

    def test_on_model_changed_does_not_restart_same_urgent_pump(self):
        hover, _window, model, _config, _tooltip, _frame = _make_hover()
        urgent = DockItem(desktop_id="u.desktop", is_urgent=True, last_urgent=123)
        model.visible_items.return_value = [urgent]
        hover.start_anim_pump = MagicMock()

        hover.on_model_changed()
        hover.on_model_changed()

        hover.start_anim_pump.assert_called_once_with(duration_ms=700)

    def test_on_model_changed_starts_pump_for_newer_urgent_timestamp(self):
        hover, _window, model, _config, _tooltip, _frame = _make_hover()
        urgent = DockItem(desktop_id="u.desktop", is_urgent=True, last_urgent=123)
        model.visible_items.return_value = [urgent]
        hover.start_anim_pump = MagicMock()

        hover.on_model_changed()
        urgent.last_urgent = 456
        hover.on_model_changed()

        assert hover.start_anim_pump.call_count == 2


class TestShowPreview:
    @pytest.mark.parametrize(
        "position",
        [
            Position.BOTTOM,
            Position.TOP,
            Position.LEFT,
            Position.RIGHT,
        ],
    )
    def test_show_preview_computes_anchor_for_positions(
        # Given
        # Then
        # When
        self,
        monkeypatch,
        position,
    ):
        hover, _window, model, config, _tooltip, _frame = _make_hover()
        item = DockItem(
            desktop_id="firefox.desktop",
            name="Firefox",
            is_running=True,
            instance_count=1,
        )
        hover.hovered_item = item
        model.visible_items.return_value = [item]
        config.pos = position
        preview = MagicMock()
        hover.set_preview(preview)

        assert hover._show_preview(item, object()) is False
        preview.show_for_item.assert_called_once()
        args = preview.show_for_item.call_args.args
        assert args[0] == "firefox.desktop"
        assert args[4] == position

    def test_show_preview_returns_false_when_not_realized_or_not_hovered(
        self, monkeypatch
    ):
        # Given
        hover, window, model, _config, _tooltip, _frame = _make_hover()
        item = DockItem(desktop_id="x.desktop", name="X")
        hover.hovered_item = None
        hover.set_preview(MagicMock())
        model.visible_items.return_value = [item]
        # Then
        # When
        assert hover._show_preview(item, object()) is False

        hover.hovered_item = item
        window.get_realized.return_value = False
        assert hover._show_preview(item, object()) is False
