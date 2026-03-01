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

import docking.ui.hover as hover_mod  # noqa: E402
from docking.core.position import Position  # noqa: E402
from docking.platform.model import DockItem  # noqa: E402


def _make_hover():
    window = MagicMock()
    window.local_cursor_main.return_value = 10.0
    window.zoomed_main_offset.return_value = 0.0
    window.get_realized.return_value = True
    window.get_position.return_value = (100, 200)
    window.get_size.return_value = (500, 60)
    window.drawing_area = MagicMock()
    model = MagicMock()
    config = SimpleNamespace(
        previews_enabled=True,
        icon_size=48,
        pos=Position.BOTTOM,
    )
    theme = SimpleNamespace(item_padding=8, h_padding=10, bottom_padding=12)
    tooltip = MagicMock()
    hover = hover_mod.HoverManager(window, config, model, theme, tooltip)
    return hover, window, model, config, tooltip


def _layout():
    return [SimpleNamespace(x=0.0, scale=1.0, width=48.0)]


class TestHoverUpdates:
    def test_update_changes_hover_and_starts_preview_timer(self, monkeypatch):
        # Given
        hover, window, model, _config, tooltip = _make_hover()
        item = DockItem(
            desktop_id="firefox.desktop",
            name="Firefox",
            is_running=True,
            instance_count=1,
        )
        model.visible_items.return_value = [item]
        window.hit_test.return_value = item
        hover.set_preview(MagicMock())
        monkeypatch.setattr(
            hover_mod, "compute_layout", lambda *args, **kwargs: _layout()
        )
        monkeypatch.setattr(hover_mod.GLib, "timeout_add", lambda _ms, _cb, *_args: 77)

        # When
        hover.update(cursor_main=20.0)
        # Then
        assert hover.hovered_item is item
        tooltip.update.assert_called_once_with(item, _layout())
        assert hover._preview_timer_id == 77

    def test_update_same_item_only_refreshes_tooltip(self, monkeypatch):
        # Given
        hover, window, model, _config, tooltip = _make_hover()
        item = DockItem(desktop_id="x.desktop", name="X")
        hover.hovered_item = item
        model.visible_items.return_value = [item]
        window.hit_test.return_value = item
        monkeypatch.setattr(
            hover_mod, "compute_layout", lambda *args, **kwargs: _layout()
        )

        hover.cancel = MagicMock()
        # When
        hover.update(cursor_main=20.0)
        # Then
        tooltip.update.assert_called_once_with(item, _layout())
        hover.cancel.assert_not_called()

    def test_update_non_running_item_schedules_hide(self, monkeypatch):
        # Given
        hover, window, model, config, _tooltip = _make_hover()
        item = DockItem(
            desktop_id="x.desktop", name="X", is_running=False, instance_count=0
        )
        model.visible_items.return_value = [item]
        window.hit_test.return_value = item
        preview = MagicMock()
        hover.set_preview(preview)
        config.previews_enabled = True
        monkeypatch.setattr(
            hover_mod, "compute_layout", lambda *args, **kwargs: _layout()
        )

        # When
        hover.update(cursor_main=20.0)
        # Then
        preview.schedule_hide.assert_called_once()


class TestHoverTimers:
    def test_cancel_removes_preview_timer(self, monkeypatch):
        # Given
        hover, _window, _model, _config, _tooltip = _make_hover()
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
        hover, window, _model, _config, _tooltip = _make_hover()
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

    def test_on_model_changed_starts_pump_for_urgent_item(self):
        # Given
        hover, _window, model, _config, _tooltip = _make_hover()
        urgent = DockItem(desktop_id="u.desktop", is_urgent=True, last_urgent=123)
        model.visible_items.return_value = [urgent]
        hover.start_anim_pump = MagicMock()

        # When
        hover.on_model_changed()
        # Then
        hover.start_anim_pump.assert_called_once_with(duration_ms=700)


class TestShowPreview:
    @pytest.mark.parametrize(
        ("position", "expected_method"),
        [
            (Position.BOTTOM, "bottom"),
            (Position.TOP, "top"),
            (Position.LEFT, "left"),
            (Position.RIGHT, "right"),
        ],
    )
    def test_show_preview_computes_anchor_for_positions(
        # Given
        # Then
        # When
        self,
        monkeypatch,
        position,
        expected_method,
    ):
        hover, window, model, config, _tooltip = _make_hover()
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
        monkeypatch.setattr(
            hover_mod, "compute_layout", lambda *args, **kwargs: _layout()
        )

        assert hover._show_preview(item, object()) is False
        preview.show_for_item.assert_called_once()
        args = preview.show_for_item.call_args.args
        assert args[0] == "firefox.desktop"
        assert args[4] == position

    def test_show_preview_returns_false_when_not_realized_or_not_hovered(
        self, monkeypatch
    ):
        # Given
        hover, window, model, _config, _tooltip = _make_hover()
        item = DockItem(desktop_id="x.desktop", name="X")
        hover.hovered_item = None
        hover.set_preview(MagicMock())
        model.visible_items.return_value = [item]
        monkeypatch.setattr(
            hover_mod, "compute_layout", lambda *args, **kwargs: _layout()
        )
        # Then
        # When
        assert hover._show_preview(item, object()) is False

        hover.hovered_item = item
        window.get_realized.return_value = False
        assert hover._show_preview(item, object()) is False
