"""Integration-style tests for DockWindow event handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.ui.dock_window as dock_window_mod
from docking.core.position import Position
from docking.platform.model import DockItem
from docking.ui.autohide import HideState


def _layout():
    return [SimpleNamespace(x=0.0, scale=1.0, width=48.0)]


def _make_stub(item: DockItem | None = None):
    item = item or DockItem(desktop_id="firefox.desktop")
    stub = SimpleNamespace()
    stub.config = SimpleNamespace(pos=Position.BOTTOM)
    stub.model = MagicMock()
    stub.model.visible_items.return_value = [item]
    stub.model.get_applet = MagicMock()
    stub.theme = SimpleNamespace(item_padding=8, h_padding=10, urgent_glow_time_ms=500)
    stub.window_tracker = MagicMock()
    stub._menu = MagicMock()
    stub._tooltip = MagicMock()
    stub._hover = MagicMock()
    stub._hover.hovered_item = item
    stub._hover.cancel = MagicMock()
    stub._preview = None
    stub.autohide = None
    stub.cursor_x = 12.0
    stub.cursor_y = 6.0
    stub._click_x = 12.0
    stub._click_y = 6.0
    stub.local_cursor_main = MagicMock(return_value=-1e6)
    stub._main_axis_cursor = MagicMock(return_value=33.0)
    stub.hit_test = MagicMock(return_value=item)
    stub._update_dock_size = MagicMock()
    return stub, item


class TestButtonReleaseFlow:
    def test_right_click_opens_context_menu(self):
        # Given
        stub, _item = _make_stub()
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_RIGHT, state=0
        )

        # When
        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )
        # Then
        assert handled is True
        stub._menu.show.assert_called_once_with(event, 33.0)

    def test_left_click_on_applet_updates_tooltip_immediately(self, monkeypatch):
        # Given
        item = DockItem(desktop_id="applet://quote")
        stub, _ = _make_stub(item=item)
        applet = MagicMock()
        stub.model.get_applet.return_value = applet
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        monkeypatch.setattr(
            dock_window_mod, "compute_layout", lambda *_a, **_k: _layout()
        )
        monkeypatch.setattr(
            dock_window_mod,
            "is_applet",
            lambda desktop_id: desktop_id.startswith("applet://"),
        )
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 999)

        # When
        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )
        # Then
        assert handled is True
        applet.on_clicked.assert_called_once()
        stub._tooltip.update.assert_called_once_with(item, _layout())
        stub._hover.start_anim_pump.assert_called_once_with(350)

    def test_left_click_running_app_toggles_focus(self, monkeypatch):
        # Given
        item = DockItem(desktop_id="firefox.desktop", is_running=True)
        stub, _ = _make_stub(item=item)
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        monkeypatch.setattr(
            dock_window_mod, "compute_layout", lambda *_a, **_k: _layout()
        )
        monkeypatch.setattr(dock_window_mod, "is_applet", lambda desktop_id: False)
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 1010)

        # When
        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )
        # Then
        assert handled is True
        stub.window_tracker.toggle_focus.assert_called_once_with("firefox.desktop")
        stub._hover.start_anim_pump.assert_called_once_with(350)
        assert item.last_clicked == 1010
        assert item.last_launched == 0

    def test_middle_click_force_launches_running_app(self, monkeypatch):
        # Given
        item = DockItem(desktop_id="firefox.desktop", is_running=True)
        stub, _ = _make_stub(item=item)
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_MIDDLE, state=0
        )
        launch_calls: list[str] = []
        monkeypatch.setattr(
            dock_window_mod, "compute_layout", lambda *_a, **_k: _layout()
        )
        monkeypatch.setattr(dock_window_mod, "is_applet", lambda desktop_id: False)
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 2020)
        monkeypatch.setattr(
            dock_window_mod,
            "launch",
            lambda desktop_id: launch_calls.append(desktop_id),
        )

        # When
        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )
        # Then
        assert handled is True
        assert launch_calls == ["firefox.desktop"]
        assert item.last_launched == 2020
        stub._hover.start_anim_pump.assert_called_once_with(700)

    def test_drag_delta_above_threshold_is_ignored(self):
        # Given
        stub, _item = _make_stub()
        stub._click_x = 0.0
        event = SimpleNamespace(
            x=40.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        # When
        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )
        # Then
        assert handled is False
        stub._menu.show.assert_not_called()


class TestScrollAndHoverFlow:
    def test_scroll_on_applet_updates_tooltip(self, monkeypatch):
        # Given
        item = DockItem(desktop_id="applet://volume")
        stub, _ = _make_stub(item=item)
        applet = MagicMock()
        stub.model.get_applet.return_value = applet
        event = SimpleNamespace(
            x=10.0,
            y=5.0,
            direction=dock_window_mod.Gdk.ScrollDirection.UP,
        )
        monkeypatch.setattr(
            dock_window_mod, "compute_layout", lambda *_a, **_k: _layout()
        )
        monkeypatch.setattr(
            dock_window_mod,
            "is_applet",
            lambda desktop_id: desktop_id.startswith("applet://"),
        )

        # When
        handled = dock_window_mod.DockWindow._on_scroll(stub, MagicMock(), event)
        # Then
        assert handled is True
        applet.on_scroll.assert_called_once_with(True)
        stub._tooltip.update.assert_called_once_with(item, _layout())

    def test_scroll_on_non_applet_returns_false(self, monkeypatch):
        # Given
        stub, _item = _make_stub()
        event = SimpleNamespace(
            x=10.0,
            y=5.0,
            direction=dock_window_mod.Gdk.ScrollDirection.DOWN,
        )
        monkeypatch.setattr(
            dock_window_mod, "compute_layout", lambda *_a, **_k: _layout()
        )
        monkeypatch.setattr(dock_window_mod, "is_applet", lambda desktop_id: False)

        # When
        handled = dock_window_mod.DockWindow._on_scroll(stub, MagicMock(), event)
        # Then
        assert handled is False


class TestLeaveEnterFlow:
    def test_leave_ignores_inferior_notify(self):
        # Given
        stub, _item = _make_stub()
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.INFERIOR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=2.0,
            y=2.0,
        )
        # When
        handled = dock_window_mod.DockWindow._on_leave(stub, MagicMock(), event)
        # Then
        assert handled is False

    def test_leave_inside_input_rect_is_ignored(self):
        # Given
        stub, _item = _make_stub()
        stub._last_input_rect = (0, 0, 100, 100)
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.NONLINEAR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=20.0,
            y=20.0,
        )

        # When
        handled = dock_window_mod.DockWindow._on_leave(stub, MagicMock(), event)
        # Then
        assert handled is False
        stub._hover.cancel.assert_not_called()

    def test_leave_clears_hover_and_resets_cursor_without_preview_or_autohide(self):
        # Given
        stub, _item = _make_stub()
        widget = MagicMock()
        stub._last_input_rect = None
        stub._preview = MagicMock()
        stub._preview.get_visible.return_value = False
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.NONLINEAR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=200.0,
            y=200.0,
        )

        # When
        handled = dock_window_mod.DockWindow._on_leave(stub, widget, event)
        # Then
        assert handled is True
        assert stub._hover.hovered_item is None
        stub._hover.cancel.assert_called_once()
        stub._tooltip.hide.assert_called_once()
        stub._preview.schedule_hide.assert_called_once()
        assert stub.cursor_x == -1.0
        assert stub.cursor_y == -1.0
        stub._update_dock_size.assert_called_once()
        widget.queue_draw.assert_called_once()

    def test_enter_sets_cursor_and_notifies_autohide(self):
        # Given
        stub, _item = _make_stub()
        stub.autohide = MagicMock()
        event = SimpleNamespace(x=44.0, y=11.0)
        # When
        handled = dock_window_mod.DockWindow._on_enter(stub, MagicMock(), event)
        # Then
        assert handled is True
        assert stub.cursor_x == 44.0
        assert stub.cursor_y == 11.0
        stub.autohide.on_mouse_enter.assert_called_once()


class TestUrgentGlow:
    def test_has_active_urgent_glow_only_when_hidden_and_recent(self, monkeypatch):
        # Given
        stub, _item = _make_stub()
        urgent = DockItem(desktop_id="urgent.desktop", last_urgent=1500)
        old = DockItem(desktop_id="old.desktop", last_urgent=1)
        stub.model.visible_items.return_value = [urgent, old]
        stub.autohide = SimpleNamespace(enabled=True, state=HideState.HIDDEN)
        stub.theme = SimpleNamespace(urgent_glow_time_ms=2)
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 3000)

        # Then
        # When
        assert dock_window_mod.DockWindow._has_active_urgent_glow(stub) is True

        stub.autohide.state = HideState.VISIBLE
        assert dock_window_mod.DockWindow._has_active_urgent_glow(stub) is False

    def test_urgent_glow_tick_requests_redraw_once(self):
        # Given
        stub, _item = _make_stub()
        stub.drawing_area = MagicMock()
        # Then
        # When
        assert dock_window_mod.DockWindow._urgent_glow_tick(stub) is False
        stub.drawing_area.queue_draw.assert_called_once()
