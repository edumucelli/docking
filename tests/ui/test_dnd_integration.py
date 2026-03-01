"""Integration-style tests for DnDHandler."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.dnd as dnd_mod  # noqa: E402
from docking.core.position import Position  # noqa: E402
from docking.platform.model import DockItem  # noqa: E402


def _layout(xs: list[float]):
    return [SimpleNamespace(x=x, scale=1.0, width=48.0) for x in xs]


def _make_handler(monkeypatch, lock_icons: bool = False):
    drawing_area = MagicMock()
    window = MagicMock()
    window.drawing_area = drawing_area
    window.local_cursor_main.return_value = 12.0
    window.zoomed_main_offset.return_value = 0.0
    window.cursor_x = 20.0
    window.cursor_y = 8.0
    window.autohide = MagicMock()

    model = MagicMock()
    config = SimpleNamespace(
        lock_icons=lock_icons,
        pos=Position.BOTTOM,
        icon_size=48,
        zoom_percent=2.0,
        pinned=[],
        save=MagicMock(),
    )
    renderer = SimpleNamespace(slide_offsets={}, prev_positions={})
    theme = SimpleNamespace(item_padding=8, h_padding=10)
    launcher = MagicMock()
    monkeypatch.setattr(dnd_mod, "show_poof", MagicMock())
    return dnd_mod.DnDHandler(window, model, config, renderer, theme, launcher)


class TestSetupAndToggle:
    def test_setup_enables_source_and_dest_when_unlocked(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch, lock_icons=False)
        da = handler._window.drawing_area
        # Then
        # When
        da.drag_source_set.assert_called_once()
        da.drag_dest_set.assert_called_once()
        # drag handlers are always connected
        assert da.connect.call_count >= 6

    def test_set_locked_toggles_dnd(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._disable_dnd = MagicMock()
        handler._enable_dnd = MagicMock()
        # When
        handler.set_locked(True)
        handler.set_locked(False)
        # Then
        handler._disable_dnd.assert_called_once()
        handler._enable_dnd.assert_called_once()


class TestDragBeginMotion:
    def test_drag_begin_sets_drag_index_and_icon(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        icon = MagicMock()
        icon.scale_simple.return_value = object()
        handler._model.visible_items.return_value = [
            DockItem(desktop_id="firefox.desktop", name="Firefox", icon=icon)
        ]
        monkeypatch.setattr(
            dnd_mod, "compute_layout", lambda *args, **kwargs: _layout([0.0])
        )
        icon_set = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_set_icon_pixbuf", icon_set)

        # When
        handler._on_drag_begin(handler._window.drawing_area, MagicMock())
        # Then
        assert handler._drag_from == 0
        assert handler.drag_index == 0
        icon_set.assert_called_once()

    def test_drag_motion_external_updates_insert_gap(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler.drop_insert_index = -1
        handler._model.visible_items.return_value = [
            DockItem("a.desktop"),
            DockItem("b.desktop"),
        ]
        monkeypatch.setattr(
            dnd_mod,
            "compute_layout",
            lambda *args, **kwargs: _layout([0.0, 70.0]),
        )
        status_calls = []
        monkeypatch.setattr(
            dnd_mod.Gdk,
            "drag_status",
            lambda _ctx, action, _time: status_calls.append(action),
        )
        widget = handler._window.drawing_area

        # When
        handled = handler._on_drag_motion(widget, MagicMock(), x=20, y=5, time=1)
        # Then
        assert handled is True
        assert handler.drop_insert_index == 0
        handler._window.autohide.on_mouse_enter.assert_called_once()
        assert status_calls

    def test_drag_motion_internal_reorders(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = 0
        handler.drag_index = 0
        handler._model.visible_items.return_value = [
            DockItem("a.desktop"),
            DockItem("b.desktop"),
        ]
        monkeypatch.setattr(
            dnd_mod,
            "compute_layout",
            lambda *args, **kwargs: _layout([0.0, 70.0]),
        )
        monkeypatch.setattr(dnd_mod.Gdk, "drag_status", lambda *_a, **_k: None)

        # When
        handled = handler._on_drag_motion(
            handler._window.drawing_area, MagicMock(), 200, 5, 1
        )
        # Then
        assert handled is True
        handler._model.reorder_visible.assert_called_once()
        assert handler.drag_index == 1


class TestDropAndReceive:
    def test_drag_drop_requests_target_data(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        widget = handler._window.drawing_area
        widget.drag_dest_find_target.return_value = "text/uri-list"

        # When
        handled = handler._on_drag_drop(widget, MagicMock(), 0, 0, 7)
        # Then
        assert handled is True
        widget.drag_get_data.assert_called_once()

    def test_drag_drop_without_target_clears_gap(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler.drop_insert_index = 2
        widget = handler._window.drawing_area
        widget.drag_dest_find_target.return_value = None

        # When
        handled = handler._on_drag_drop(widget, MagicMock(), 0, 0, 7)
        # Then
        assert handled is False
        assert handler.drop_insert_index == -1
        widget.queue_draw.assert_called_once()

    def test_drag_data_received_internal_finishes_immediately(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = 0
        finish = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_finish", finish)

        # When
        handler._on_drag_data_received(
            handler._window.drawing_area,
            MagicMock(),
            0,
            0,
            MagicMock(),
            0,
            123,
        )
        # Then
        finish.assert_called_once_with(ANY, True, False, 123)

    def test_drag_data_received_external_adds_pinned_item(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler.drop_insert_index = 0
        handler._model.pinned_items = []
        handler._model.find_by_desktop_id.return_value = None
        resolved = SimpleNamespace(
            name="Firefox",
            icon_name="firefox",
            wm_class="firefox",
        )
        handler._launcher.resolve.return_value = resolved
        handler._launcher.load_icon.return_value = object()
        selection = MagicMock()
        selection.get_uris.return_value = [
            "file:///usr/share/applications/firefox.desktop"
        ]
        finish = MagicMock()
        monkeypatch.setattr(dnd_mod.Gtk, "drag_finish", finish)

        # When
        handler._on_drag_data_received(
            handler._window.drawing_area,
            MagicMock(),
            0,
            0,
            selection,
            1,
            77,
        )
        # Then
        assert handler._config.pinned == ["firefox.desktop"]
        assert len(handler._model.pinned_items) == 1
        handler._config.save.assert_called_once()
        handler._model.sync_pinned_to_config.assert_called_once()
        handler._model.notify.assert_called_once()
        finish.assert_called_once_with(ANY, True, False, 77)


class TestDragLeaveEnd:
    def test_drag_leave_schedules_deferred_clear_and_autohide(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler.drop_insert_index = 1
        timeout_calls = []
        monkeypatch.setattr(
            dnd_mod.GLib,
            "timeout_add",
            lambda delay, cb, widget: timeout_calls.append((delay, cb, widget)) or 1,
        )
        widget = handler._window.drawing_area

        # When
        handler._on_drag_leave(widget, MagicMock(), 0)
        # Then
        assert timeout_calls and timeout_calls[0][0] == 100
        handler._window.autohide.on_mouse_leave.assert_called_once()
        widget.queue_draw.assert_called()

    def test_deferred_clear_drop_gap(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = -1
        handler.drop_insert_index = 2
        widget = handler._window.drawing_area
        # Then
        # When
        assert handler._deferred_clear_drop_gap(widget) is False
        assert handler.drop_insert_index == -1
        widget.queue_draw.assert_called_once()

    def test_drag_end_unpins_when_dropped_outside(self, monkeypatch):
        # Given
        handler = _make_handler(monkeypatch)
        handler._drag_from = 0
        handler.drag_index = 0
        pinned = DockItem(desktop_id="firefox.desktop", is_pinned=True, name="Firefox")
        handler._model.visible_items.return_value = [pinned]
        pointer = MagicMock()
        pointer.get_position.return_value = (None, 200, 50)
        seat = MagicMock()
        seat.get_pointer.return_value = pointer
        display = MagicMock()
        display.get_default_seat.return_value = seat
        handler._window.get_display.return_value = display
        handler._window.get_position.return_value = (100, 200)
        handler._window.get_size.return_value = (400, 60)
        widget = handler._window.drawing_area

        # When
        handler._on_drag_end(widget, MagicMock())
        # Then
        handler._model.unpin_item.assert_called_once_with("firefox.desktop")
        assert handler._renderer.slide_offsets == {}
        assert handler._renderer.prev_positions == {}
        handler._config.save.assert_called()
        widget.queue_draw.assert_called()
