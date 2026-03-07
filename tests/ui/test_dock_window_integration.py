"""Integration-style tests for DockWindow event handlers."""

from __future__ import annotations

from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

import docking.ui.dock_window as dock_window_mod
from docking.core.items import FILE_KIND, FOLDER_KIND
from docking.core.position import Position
from docking.platform.model import DockItem
from docking.ui.autohide import HideState
from docking.ui.geometry import Rect
from docking.ui.interaction import DockInteractionCoordinator


@pytest.fixture(autouse=True)
def _route_stub_geometry(monkeypatch):
    original_capture = dock_window_mod.capture_geometry_inputs
    original_build = dock_window_mod.build_geometry_frame_from_inputs

    def _capture(window, **kwargs):
        frame = getattr(window, "_test_geometry_frame", None)
        if frame is not None:
            return frame
        return original_capture(window, **kwargs)

    def _build(inputs):
        if hasattr(inputs, "cursor_rect"):
            return inputs
        return original_build(inputs)

    monkeypatch.setattr(dock_window_mod, "capture_geometry_inputs", _capture)
    monkeypatch.setattr(dock_window_mod, "build_geometry_frame_from_inputs", _build)


def _make_stub(item: DockItem | None = None):
    item = item or DockItem(desktop_id="firefox.desktop")
    frame = SimpleNamespace(
        cursor_rect=Rect(0, 0, 100, 100),
        item_at_point=MagicMock(return_value=item),
        geometry_for_item=MagicMock(return_value=None),
    )
    stub = SimpleNamespace()
    stub.config = SimpleNamespace(pos=Position.BOTTOM)
    stub.model = MagicMock()
    stub.model.visible_items.return_value = [item]
    stub.model.get_applet = MagicMock()
    stub.theme = SimpleNamespace(item_padding=8, h_padding=10, urgent_glow_time_ms=500)
    stub.window_tracker = MagicMock()
    stub._menu = MagicMock()
    stub.tooltip = MagicMock()
    stub._hover = MagicMock()
    stub._hover.hovered_item = item
    stub._hover.cancel = MagicMock()
    stub.preview = None
    stub._menu_popup_visible = False
    stub.autohide = None
    stub.cursor_x = 12.0
    stub.cursor_y = 6.0
    stub._click_x = 12.0
    stub._click_y = 6.0
    stub.local_cursor_main = MagicMock(return_value=-1e6)
    stub._main_axis_cursor = MagicMock(return_value=33.0)
    stub.hit_test = MagicMock(return_value=item)
    stub.update_dock_size = MagicMock()
    stub.drawing_area = MagicMock()
    stub._pointer_inside_input_rect = MagicMock(return_value=False)
    stub._test_geometry_frame = frame
    stub._current_geometry_frame = frame
    stub._applied_input_frame = frame
    stub.dock_hovered = True
    stub.interaction = MagicMock()
    stub._on_effective_enter = MethodType(
        dock_window_mod.DockWindow._on_effective_enter, stub
    )
    stub._on_effective_leave = MethodType(
        dock_window_mod.DockWindow._on_effective_leave, stub
    )
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
        stub.tooltip.update.assert_called_once_with(item, stub._test_geometry_frame)
        stub._hover.start_anim_pump.assert_called_once_with(350)

    def test_left_click_running_app_toggles_focus(self, monkeypatch):
        # Given
        item = DockItem(desktop_id="firefox.desktop", is_running=True)
        stub, _ = _make_stub(item=item)
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
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

    def test_left_click_file_item_opens_target(self, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/notes.txt",
            kind=FILE_KIND,
            target="file:///tmp/notes.txt",
        )
        stub, _ = _make_stub(item=item)
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 3030)
        opened: list[str] = []
        monkeypatch.setattr(
            dock_window_mod, "open_target", lambda target: opened.append(target)
        )

        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )

        assert handled is True
        assert opened == ["file:///tmp/notes.txt"]
        assert item.last_launched == 3030

    def test_left_click_folder_item_opens_item_menu(self, monkeypatch):
        item = DockItem(
            desktop_id="file:///tmp/docs",
            kind=FOLDER_KIND,
            target="file:///tmp/docs",
        )
        stub, _ = _make_stub(item=item)
        event = SimpleNamespace(
            x=12.0, y=6.0, button=dock_window_mod.MOUSE_LEFT, state=0
        )
        monkeypatch.setattr(dock_window_mod.GLib, "get_monotonic_time", lambda: 4040)

        handled = dock_window_mod.DockWindow._on_button_release(
            stub, MagicMock(), event
        )

        assert handled is True
        stub._menu.show_item.assert_called_once_with(event, item)

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
            dock_window_mod,
            "is_applet",
            lambda desktop_id: desktop_id.startswith("applet://"),
        )

        # When
        handled = dock_window_mod.DockWindow._on_scroll(stub, MagicMock(), event)
        # Then
        assert handled is True
        applet.on_scroll.assert_called_once_with(True)
        stub.tooltip.update.assert_called_once_with(item, stub._test_geometry_frame)

    def test_scroll_on_non_applet_returns_false(self, monkeypatch):
        # Given
        stub, _item = _make_stub()
        event = SimpleNamespace(
            x=10.0,
            y=5.0,
            direction=dock_window_mod.Gdk.ScrollDirection.DOWN,
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
        stub._current_geometry_frame = SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100))
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
        stub.interaction.point_inside_event_frame.assert_called_once_with(
            x=20.0, y=20.0
        )
        stub._hover.cancel.assert_not_called()

    def test_leave_clears_hover_and_resets_cursor_without_preview_or_autohide(self):
        # Given
        stub, _item = _make_stub()
        widget = MagicMock()
        stub._current_geometry_frame = None
        stub._applied_input_frame = None
        stub.preview = MagicMock()
        stub.preview.get_visible.return_value = False
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
        stub.interaction.on_effective_leave.assert_called_once_with(widget)

    def test_leave_with_visible_preview_defers_autohide_until_preview_hides(self):
        stub, _item = _make_stub()
        widget = MagicMock()
        stub._current_geometry_frame = None
        stub._applied_input_frame = None
        stub.preview = MagicMock()
        stub.preview.get_visible.return_value = True
        stub.autohide = SimpleNamespace(
            enabled=True,
            on_mouse_leave=MagicMock(),
            set_hovered=MagicMock(),
            set_disabled=MagicMock(),
        )
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.NONLINEAR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=200.0,
            y=200.0,
        )

        handled = dock_window_mod.DockWindow._on_leave(stub, widget, event)

        assert handled is True
        stub.interaction.on_effective_leave.assert_called_once_with(widget)

    def test_leave_with_autohide_keeps_hover_identity_until_hidden(self):
        stub, item = _make_stub()
        widget = MagicMock()
        stub._current_geometry_frame = None
        stub._applied_input_frame = None
        stub.autohide = SimpleNamespace(
            enabled=True,
            on_mouse_leave=MagicMock(),
            set_hovered=MagicMock(),
            set_disabled=MagicMock(),
        )
        event = SimpleNamespace(
            detail=dock_window_mod.Gdk.NotifyType.NONLINEAR,
            mode=dock_window_mod.Gdk.CrossingMode.NORMAL,
            x=200.0,
            y=200.0,
        )

        handled = dock_window_mod.DockWindow._on_leave(stub, widget, event)

        assert handled is True
        stub.interaction.on_effective_leave.assert_called_once_with(widget)

    def test_enter_sets_cursor_and_notifies_autohide(self):
        # Given
        stub, _item = _make_stub()
        stub.autohide = MagicMock()
        stub.dock_hovered = False
        event = SimpleNamespace(x=44.0, y=11.0)
        # When
        handled = dock_window_mod.DockWindow._on_enter(stub, MagicMock(), event)
        # Then
        assert handled is True
        assert stub.cursor_x == 44.0
        assert stub.cursor_y == 11.0
        stub.interaction.on_effective_enter.assert_called_once()

    def test_enter_does_not_trigger_effective_enter_when_outside_cursor_rect(self):
        stub, _item = _make_stub()
        stub.autohide = MagicMock()
        stub.dock_hovered = False
        outside_frame = SimpleNamespace(cursor_rect=Rect(292, 70, 1335, 148))
        stub._test_geometry_frame = outside_frame
        event = SimpleNamespace(x=556.0, y=3.0)

        handled = dock_window_mod.DockWindow._on_enter(stub, MagicMock(), event)

        assert handled is True
        assert stub.cursor_x == 556.0
        assert stub.cursor_y == 3.0
        stub.interaction.on_effective_enter.assert_not_called()
        assert stub.dock_hovered is False


class TestMenuPopupFlow:
    def test_menu_close_triggers_autohide_when_pointer_outside(self):
        # Given
        stub, _item = _make_stub()
        stub._menu_popup_visible = True
        stub.autohide = SimpleNamespace(
            enabled=True,
            on_mouse_leave=MagicMock(),
            set_hovered=MagicMock(),
            set_disabled=MagicMock(),
        )
        stub.preview = MagicMock()
        stub.preview.get_visible.return_value = False

        # When
        dock_window_mod.DockWindow.on_menu_popup_closed(stub)

        # Then
        stub.interaction.menu_popup_closed.assert_called_once()

    def test_menu_close_with_visible_preview_defers_autohide(self):
        stub, _item = _make_stub()
        stub._menu_popup_visible = True
        stub.autohide = SimpleNamespace(
            enabled=True,
            on_mouse_leave=MagicMock(),
            set_hovered=MagicMock(),
            set_disabled=MagicMock(),
        )
        stub.preview = MagicMock()
        stub.preview.get_visible.return_value = True

        dock_window_mod.DockWindow.on_menu_popup_closed(stub)

        stub.interaction.menu_popup_closed.assert_called_once()

    def test_menu_close_does_not_hide_when_pointer_is_back_on_dock(self):
        # Given
        stub, _item = _make_stub()
        stub._menu_popup_visible = True
        stub.autohide = SimpleNamespace(
            enabled=True,
            on_mouse_leave=MagicMock(),
            set_hovered=MagicMock(),
            set_disabled=MagicMock(),
        )
        dock_window_mod.DockWindow.on_menu_popup_closed(stub)

        stub.interaction.menu_popup_closed.assert_called_once()

    def test_menu_close_is_noop_when_no_popup_is_tracked(self):
        # Given
        stub, _item = _make_stub()
        stub.autohide = SimpleNamespace(
            enabled=True,
            on_mouse_leave=MagicMock(),
            set_hovered=MagicMock(),
            set_disabled=MagicMock(),
        )

        # When
        dock_window_mod.DockWindow.on_menu_popup_closed(stub)

        # Then
        stub.interaction.menu_popup_closed.assert_called_once()


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


class TestModelChangedFlow:
    def test_model_changed_refreshes_hover_and_redraw(self):
        # Given
        stub, _item = _make_stub()
        stub.drawing_area = MagicMock()

        # When
        dock_window_mod.DockWindow._on_model_changed(stub)

        # Then
        stub.update_dock_size.assert_called_once()
        stub._hover.on_model_changed.assert_called_once()
        stub._hover.update.assert_called_once_with(33.0)
        stub.drawing_area.queue_draw.assert_called_once()

    def test_model_changed_skips_hover_refresh_when_not_hovering(self):
        # Given
        stub, _item = _make_stub()
        stub.drawing_area = MagicMock()
        stub._hover.hovered_item = None

        # When
        dock_window_mod.DockWindow._on_model_changed(stub)

        # Then
        stub.update_dock_size.assert_called_once()
        stub._hover.on_model_changed.assert_called_once()
        stub._hover.update.assert_not_called()
        stub.drawing_area.queue_draw.assert_called_once()


class TestDockWindowSetupAndGeometry:
    def test_setup_window_applies_hints_and_connects(self):
        # Given
        screen = SimpleNamespace(
            get_rgba_visual=lambda: None,
            get_system_visual=lambda: "sys-visual",
            connect=MagicMock(side_effect=[11, 12]),
            disconnect=MagicMock(),
        )
        stub = SimpleNamespace(
            set_title=MagicMock(),
            set_decorated=MagicMock(),
            set_skip_taskbar_hint=MagicMock(),
            set_skip_pager_hint=MagicMock(),
            stick=MagicMock(),
            set_keep_above=MagicMock(),
            set_type_hint=MagicMock(),
            set_app_paintable=MagicMock(),
            set_resizable=MagicMock(),
            get_screen=MagicMock(return_value=screen),
            set_visual=MagicMock(),
            connect=MagicMock(),
            placement=SimpleNamespace(
                attach_screen_signals=MagicMock(),
                on_realize=MagicMock(),
                on_screen_changed=MagicMock(),
                on_scale_factor_changed=MagicMock(),
                on_destroy=MagicMock(),
            ),
        )

        # When
        dock_window_mod.DockWindow._setup_window(stub)

        # Then
        stub.set_title.assert_called_once_with("Docking")
        stub.set_visual.assert_called_once_with("sys-visual")
        assert stub.connect.call_count == 5
        stub.placement.attach_screen_signals.assert_called_once_with(screen)

    def test_setup_drawing_area_initializes_events(self, monkeypatch):
        # Given
        class FakeDrawingArea:
            def __init__(self):
                self.double_buffered = None
                self.events = None
                self.connected: list[str] = []

            def set_double_buffered(self, value: bool):
                self.double_buffered = value

            def set_events(self, events: int):
                self.events = events

            def connect(self, signal: str, _callback):
                self.connected.append(signal)

        monkeypatch.setattr(
            dock_window_mod.Gtk,
            "DrawingArea",
            FakeDrawingArea,
            raising=False,
        )
        stub = SimpleNamespace(
            add=MagicMock(),
            _on_draw=MagicMock(),
            _on_motion=MagicMock(),
            _on_button_press=MagicMock(),
            _on_button_release=MagicMock(),
            _on_leave=MagicMock(),
            _on_enter=MagicMock(),
            _on_scroll=MagicMock(),
            _current_geometry_frame="sentinel-current",
            _applied_input_frame="sentinel-applied",
        )

        # When
        dock_window_mod.DockWindow._setup_drawing_area(stub)

        # Then
        assert isinstance(stub.drawing_area, FakeDrawingArea)
        assert stub.drawing_area.double_buffered is False
        assert "draw" in stub.drawing_area.connected
        assert "scroll-event" in stub.drawing_area.connected
        assert stub._current_geometry_frame == "sentinel-current"
        assert stub._applied_input_frame == "sentinel-applied"

    def test_connect_model_sets_on_change_handler(self):
        # Given
        stub = SimpleNamespace(
            model=SimpleNamespace(on_change=None), _on_model_changed=lambda: None
        )

        # When
        dock_window_mod.DockWindow._connect_model(stub)

        # Then
        assert stub.model.on_change == stub._on_model_changed


class TestDockWindowStrutsAndRegion:
    def testupdate_input_region_applies_shape_and_caches_rect(self, monkeypatch):
        # Given
        gdk_window = MagicMock()
        frame = SimpleNamespace(cursor_rect=Rect(140, 36, 120, 54))
        stub = SimpleNamespace(
            get_window=lambda: gdk_window,
            _test_geometry_frame=frame,
            _current_geometry_frame=None,
            _applied_input_frame=None,
        )

        # When
        dock_window_mod.DockWindow.update_input_region(stub)
        first_rect = stub._current_geometry_frame.cursor_rect
        dock_window_mod.DockWindow.update_input_region(stub)

        # Then
        assert first_rect is not None
        assert stub._applied_input_frame.cursor_rect == first_rect
        gdk_window.input_shape_combine_region.assert_called_once()


class TestDockWindowDrawAndHelpers:
    def test_on_draw_invokes_renderer_and_updates_region(self):
        # Given
        stub = SimpleNamespace(
            autohide=None,
            _last_autohide_state=None,
            dock_hovered=False,
            dnd=None,
            _hover=SimpleNamespace(hovered_item=None),
            renderer=SimpleNamespace(draw=MagicMock()),
            model=MagicMock(),
            config=MagicMock(),
            theme=MagicMock(),
            tooltip=MagicMock(),
            _main_axis_cursor=lambda: 12.0,
            _test_geometry_frame=SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100)),
            update_input_region=MagicMock(),
            _has_active_urgent_glow=lambda: False,
            cursor_x=1.0,
            cursor_y=2.0,
        )

        # When
        result = dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        # Then
        assert result is True
        stub.renderer.draw.assert_called_once()
        stub.update_input_region.assert_called_once()

    def test_on_draw_resets_cursor_when_hidden(self):
        # Given
        hovered = DockItem(desktop_id="hovered.desktop")
        stub = SimpleNamespace(
            autohide=SimpleNamespace(
                enabled=True, state=HideState.HIDDEN, hide_offset=0.0, zoom_progress=0.0
            ),
            dnd=None,
            _hover=SimpleNamespace(hovered_item=hovered),
            renderer=SimpleNamespace(draw=MagicMock()),
            model=MagicMock(),
            config=MagicMock(),
            theme=MagicMock(),
            tooltip=MagicMock(),
            _main_axis_cursor=lambda: -1.0,
            _test_geometry_frame=SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100)),
            update_input_region=MagicMock(),
            _has_active_urgent_glow=lambda: False,
            cursor_x=25.0,
            cursor_y=33.0,
        )

        # When
        dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        # Then
        assert stub.cursor_x == -1.0
        assert stub.cursor_y == -1.0
        assert stub._hover.hovered_item is None
        stub.tooltip.hide.assert_called_once()

    def test_on_draw_refreshes_tooltip_once_when_showing_finishes(self):
        hovered = DockItem(desktop_id="hovered.desktop")
        frame = SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100))
        stub = SimpleNamespace(
            autohide=SimpleNamespace(
                enabled=True,
                state=HideState.VISIBLE,
                hide_offset=0.0,
                zoom_progress=1.0,
            ),
            _last_autohide_state=HideState.SHOWING,
            dock_hovered=True,
            dnd=None,
            _hover=SimpleNamespace(hovered_item=hovered, update=MagicMock()),
            renderer=SimpleNamespace(draw=MagicMock()),
            model=MagicMock(),
            config=MagicMock(),
            theme=MagicMock(),
            tooltip=MagicMock(),
            _main_axis_cursor=lambda: 12.0,
            _test_geometry_frame=frame,
            update_input_region=MagicMock(),
            _has_active_urgent_glow=lambda: False,
            cursor_x=25.0,
            cursor_y=33.0,
        )

        dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        stub._hover.update.assert_called_once_with(12.0, frame=frame)
        assert stub._last_autohide_state == HideState.VISIBLE

    def test_on_motion_updates_cursor_and_hover(self):
        # Given
        widget = MagicMock()
        stub = SimpleNamespace(
            cursor_x=-1.0,
            cursor_y=-1.0,
            dock_hovered=False,
            _test_geometry_frame=SimpleNamespace(cursor_rect=Rect(0, 0, 100, 100)),
            update_dock_size=MagicMock(),
            _hover=SimpleNamespace(update=MagicMock()),
            _main_axis_cursor=lambda: 12.0,
            autohide=None,
        )
        stub.interaction = DockInteractionCoordinator(stub)
        stub._on_effective_enter = MethodType(
            dock_window_mod.DockWindow._on_effective_enter, stub
        )
        stub._on_effective_leave = MethodType(
            dock_window_mod.DockWindow._on_effective_leave, stub
        )
        event = SimpleNamespace(x=7.0, y=9.0)

        # When
        handled = dock_window_mod.DockWindow._on_motion(stub, widget, event)

        # Then
        assert handled is False
        assert stub.cursor_x == 7.0
        assert stub.cursor_y == 9.0
        assert stub.dock_hovered is True
        widget.queue_draw.assert_called_once()

    def test_on_button_press_records_click_state(self):
        # Given
        stub = SimpleNamespace(_click_x=0.0, _click_y=0.0, _click_button=0)
        event = SimpleNamespace(x=11.0, y=22.0, button=3)

        # When
        handled = dock_window_mod.DockWindow._on_button_press(stub, MagicMock(), event)

        # Then
        assert handled is False
        assert stub._click_x == 11.0
        assert stub._click_y == 22.0
        assert stub._click_button == 3

    def test_main_axis_cursor_uses_position_axis(self):
        # Given
        item = DockItem(desktop_id="a.desktop")
        stub, _ = _make_stub(item=item)
        stub.cursor_x = 40.0
        stub.cursor_y = 20.0

        # Then
        assert dock_window_mod.DockWindow._main_axis_cursor(stub) == 40.0
        stub.config.pos = Position.LEFT
        assert dock_window_mod.DockWindow._main_axis_cursor(stub) == 20.0

    def test_queue_redraw(self):
        drawing_area = MagicMock()
        stub = SimpleNamespace(
            drawing_area=drawing_area,
        )

        dock_window_mod.DockWindow.queue_redraw(stub)

        assert drawing_area.queue_draw.call_count == 1
