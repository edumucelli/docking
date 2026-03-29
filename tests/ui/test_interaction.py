"""Tests for DockInteractionCoordinator policy behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.core.position import Position
from docking.platform.model import DockItem
from docking.ui.geometry import Rect
from docking.ui.interaction import DockInteractionCoordinator


def _autohide(*, enabled: bool = True):
    return SimpleNamespace(
        enabled=enabled,
        on_mouse_leave=MagicMock(),
        on_mouse_enter=MagicMock(),
        set_hovered=MagicMock(),
        set_disabled=MagicMock(),
    )


def _make_window(item: DockItem | None = None):
    item = item or DockItem(desktop_id="firefox.desktop")
    frame = SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100))
    window = SimpleNamespace(
        config=SimpleNamespace(pos=Position.BOTTOM),
        model=MagicMock(),
        theme=SimpleNamespace(item_padding=8, h_padding=10, urgent_glow_time_ms=500),
        _menu=MagicMock(),
        tooltip=MagicMock(),
        hover=MagicMock(),
        preview=None,
        _menu_popup_visible=False,
        autohide=_autohide(enabled=False),
        cursor_x=12.0,
        cursor_y=6.0,
        update_input_region=MagicMock(),
        drawing_area=MagicMock(),
        current_geometry_frame=frame,
        applied_input_frame=frame,
        dock_hovered=True,
        get_realized=MagicMock(return_value=True),
        get_display=MagicMock(return_value=None),
        get_position=MagicMock(return_value=(0, 0)),
        zoom_animator=MagicMock(),
    )
    window.hover.hovered_item = item
    window.hover.cancel = MagicMock()
    return window, item


class TestEffectiveLeavePolicy:
    def test_leave_clears_hover_and_resets_cursor_without_preview_or_autohide(self):
        window, _item = _make_window()
        widget = MagicMock()
        window.current_geometry_frame = None
        window.applied_input_frame = None
        window.preview = MagicMock()
        window.preview.get_visible.return_value = False
        coordinator = DockInteractionCoordinator(window)

        coordinator.on_effective_leave(widget)

        assert window.hover.hovered_item is None
        window.hover.cancel.assert_called_once()
        window.tooltip.hide.assert_called_once()
        window.preview.schedule_hide.assert_not_called()
        assert window.cursor_x == -1.0
        assert window.cursor_y == -1.0
        window.update_input_region.assert_called_once()
        widget.queue_draw.assert_called_once()

    def test_leave_with_visible_preview_defers_autohide_until_preview_hides(self):
        window, _item = _make_window()
        widget = MagicMock()
        window.current_geometry_frame = None
        window.applied_input_frame = None
        window.preview = MagicMock()
        window.preview.get_visible.return_value = True
        window.autohide = _autohide()
        coordinator = DockInteractionCoordinator(window)

        coordinator.on_effective_leave(widget)

        window.preview.schedule_hide.assert_called_once()
        window.autohide.on_mouse_leave.assert_not_called()
        assert window.hover.hovered_item is not None
        assert window.cursor_x == 12.0
        assert window.cursor_y == 6.0

    def test_leave_with_autohide_keeps_hover_identity_until_hidden(self):
        window, item = _make_window()
        widget = MagicMock()
        window.current_geometry_frame = None
        window.applied_input_frame = None
        window.autohide = _autohide()
        coordinator = DockInteractionCoordinator(window)

        coordinator.on_effective_leave(widget)

        assert window.hover.hovered_item is item
        assert window.cursor_x == 12.0
        assert window.cursor_y == 6.0
        window.autohide.on_mouse_leave.assert_called_once()

    def test_effective_enter_notifies_autohide(self):
        window, _item = _make_window()
        window.autohide = _autohide()
        window.dock_hovered = False
        coordinator = DockInteractionCoordinator(window)

        coordinator.on_effective_enter()

        assert window.dock_hovered is True
        window.autohide.on_mouse_enter.assert_called_once()


class TestMenuPopupPolicy:
    def test_menu_popup_opened_disables_autohide(self):
        window, _item = _make_window()
        window.autohide = _autohide()
        coordinator = DockInteractionCoordinator(window)

        coordinator.menu_popup_opened()

        assert coordinator.dock_hovered is True
        assert window._menu_popup_visible is True
        window.autohide.set_disabled.assert_called_once_with(True, reason="menu-open")

    def test_menu_close_triggers_autohide_when_pointer_outside(self):
        window, _item = _make_window()
        window._menu_popup_visible = True
        window.autohide = _autohide()
        window.preview = MagicMock()
        window.preview.get_visible.return_value = False
        coordinator = DockInteractionCoordinator(window)
        coordinator.pointer_inside_input_rect = MagicMock(return_value=False)

        coordinator.menu_popup_closed()

        assert window._menu_popup_visible is False
        window.hover.cancel.assert_called_once()
        window.tooltip.hide.assert_called_once()
        window.preview.schedule_hide.assert_not_called()
        window.update_input_region.assert_called_once()
        window.drawing_area.queue_draw.assert_called_once()
        window.autohide.set_hovered.assert_called_once_with(False)
        window.autohide.set_disabled.assert_called_once_with(
            False, reason="menu-close-pointer-outside"
        )
        window.autohide.on_mouse_leave.assert_called_once()

    def test_menu_close_with_visible_preview_defers_autohide(self):
        window, _item = _make_window()
        window._menu_popup_visible = True
        window.autohide = _autohide()
        window.preview = MagicMock()
        window.preview.get_visible.return_value = True
        coordinator = DockInteractionCoordinator(window)
        coordinator.pointer_inside_input_rect = MagicMock(return_value=False)

        coordinator.menu_popup_closed()

        assert window._menu_popup_visible is False
        window.preview.schedule_hide.assert_called_once()
        window.autohide.set_hovered.assert_called_once_with(False)
        window.autohide.set_disabled.assert_called_once_with(
            False, reason="menu-close-pointer-outside"
        )
        window.autohide.on_mouse_leave.assert_not_called()

    def test_menu_close_does_not_hide_when_pointer_is_back_on_dock(self):
        window, _item = _make_window()
        window._menu_popup_visible = True
        window.autohide = _autohide()
        coordinator = DockInteractionCoordinator(window)
        coordinator.pointer_inside_input_rect = MagicMock(return_value=True)

        coordinator.menu_popup_closed()

        assert window._menu_popup_visible is False
        window.autohide.set_hovered.assert_called_once_with(True)
        window.autohide.set_disabled.assert_called_once_with(
            False, reason="menu-close-pointer-inside"
        )
        window.autohide.on_mouse_leave.assert_not_called()
        window.hover.cancel.assert_not_called()

    def test_menu_close_is_noop_when_no_popup_is_tracked(self):
        window, _item = _make_window()
        window.autohide = _autohide()
        coordinator = DockInteractionCoordinator(window)

        coordinator.menu_popup_closed()

        window.autohide.on_mouse_leave.assert_not_called()

    def test_menu_close_returns_early_when_autohide_is_disabled(self):
        window, _item = _make_window()
        window._menu_popup_visible = True
        window.autohide = _autohide(enabled=False)
        coordinator = DockInteractionCoordinator(window)

        coordinator.menu_popup_closed()

        assert window._menu_popup_visible is False
        window.hover.cancel.assert_not_called()
        window.autohide.on_mouse_leave.assert_not_called()


class TestPointerContainment:
    def test_dock_hovered_property_proxies_window(self):
        window, _item = _make_window()
        coordinator = DockInteractionCoordinator(window)

        assert coordinator.dock_hovered is True
        coordinator.dock_hovered = False

        assert window.dock_hovered is False

    def test_pointer_inside_input_rect_returns_false_without_display(self):
        window, _item = _make_window()
        window.get_display.return_value = None
        coordinator = DockInteractionCoordinator(window)

        assert coordinator.pointer_inside_input_rect() is False

    def test_pointer_inside_input_rect_returns_false_without_seat(self):
        window, _item = _make_window()
        display = SimpleNamespace(get_default_seat=lambda: None)
        window.get_display.return_value = display
        coordinator = DockInteractionCoordinator(window)

        assert coordinator.pointer_inside_input_rect() is False

    def test_pointer_inside_input_rect_returns_false_without_pointer(self):
        window, _item = _make_window()
        seat = SimpleNamespace(get_pointer=lambda: None)
        display = SimpleNamespace(get_default_seat=lambda: seat)
        window.get_display.return_value = display
        coordinator = DockInteractionCoordinator(window)

        assert coordinator.pointer_inside_input_rect() is False

    def test_pointer_inside_input_rect_returns_false_on_position_error(self):
        window, _item = _make_window()
        pointer = SimpleNamespace(
            get_position=MagicMock(side_effect=RuntimeError("boom"))
        )
        seat = SimpleNamespace(get_pointer=lambda: pointer)
        display = SimpleNamespace(get_default_seat=lambda: seat)
        window.get_display.return_value = display
        coordinator = DockInteractionCoordinator(window)

        assert coordinator.pointer_inside_input_rect() is False

    def test_point_inside_event_frame_returns_false_without_input_rect(self):
        window, _item = _make_window()
        window.current_geometry_frame = None
        window.applied_input_frame = None
        coordinator = DockInteractionCoordinator(window)

        assert coordinator.point_inside_event_frame(x=2.0, y=3.0) is False
