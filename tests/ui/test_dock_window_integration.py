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


class TestModelChangedFlow:
    def test_model_changed_refreshes_hover_and_redraw(self):
        # Given
        stub, _item = _make_stub()
        stub.drawing_area = MagicMock()

        # When
        dock_window_mod.DockWindow._on_model_changed(stub)

        # Then
        stub._update_dock_size.assert_called_once()
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
        stub._update_dock_size.assert_called_once()
        stub._hover.on_model_changed.assert_called_once()
        stub._hover.update.assert_not_called()
        stub.drawing_area.queue_draw.assert_called_once()


class TestDockWindowSetupAndGeometry:
    def test_setup_window_applies_hints_and_connects(self):
        # Given
        screen = SimpleNamespace(
            get_rgba_visual=lambda: None,
            get_system_visual=lambda: "sys-visual",
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
            _on_realize=MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._setup_window(stub)

        # Then
        stub.set_title.assert_called_once_with("Docking")
        stub.set_visual.assert_called_once_with("sys-visual")
        assert stub.connect.call_count == 2

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
        )

        # When
        dock_window_mod.DockWindow._setup_drawing_area(stub)

        # Then
        assert isinstance(stub.drawing_area, FakeDrawingArea)
        assert stub.drawing_area.double_buffered is False
        assert "draw" in stub.drawing_area.connected
        assert "scroll-event" in stub.drawing_area.connected
        assert stub._last_input_rect is None

    def test_connect_model_sets_on_change_handler(self):
        # Given
        stub = SimpleNamespace(
            model=SimpleNamespace(on_change=None), _on_model_changed=lambda: None
        )

        # When
        dock_window_mod.DockWindow._connect_model(stub)

        # Then
        assert stub.model.on_change == stub._on_model_changed

    def test_on_realize_calls_position_struts_and_input_update(self):
        # Given
        stub = SimpleNamespace(
            _position_dock=MagicMock(),
            _set_struts=MagicMock(),
            _update_input_region=MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._on_realize(stub, MagicMock())

        # Then
        stub._position_dock.assert_called_once()
        stub._set_struts.assert_called_once()
        stub._update_input_region.assert_called_once()

    def test_position_dock_horizontal_bottom(self):
        # Given
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1056)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        stub = SimpleNamespace(
            get_display=lambda: display,
            config=SimpleNamespace(
                icon_size=48,
                zoom_enabled=True,
                zoom_percent=1.2,
                pos=Position.BOTTOM,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.5,
            ),
            set_size_request=MagicMock(),
            resize=MagicMock(),
            move=MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._position_dock(stub)

        # Then
        stub.set_size_request.assert_called_once()
        stub.resize.assert_called_once()
        stub.move.assert_called_once()

    def test_position_dock_vertical_right(self):
        # Given
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1000)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        stub = SimpleNamespace(
            get_display=lambda: display,
            config=SimpleNamespace(
                icon_size=48,
                zoom_enabled=False,
                zoom_percent=1.0,
                pos=Position.RIGHT,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.5,
            ),
            set_size_request=MagicMock(),
            resize=MagicMock(),
            move=MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._position_dock(stub)

        # Then
        stub.move.assert_called_once()


class TestDockWindowStrutsAndRegion:
    def test_set_struts_clears_when_autohide_enabled(self):
        # Given
        stub = SimpleNamespace(
            config=SimpleNamespace(autohide=True), _clear_struts=MagicMock()
        )

        # When
        dock_window_mod.DockWindow._set_struts(stub)

        # Then
        stub._clear_struts.assert_called_once()

    def test_set_struts_returns_when_no_window(self):
        # Given
        stub = SimpleNamespace(
            config=SimpleNamespace(autohide=False),
            get_window=lambda: None,
        )

        # When / Then
        dock_window_mod.DockWindow._set_struts(stub)

    def test_set_struts_calls_platform_helper_for_x11(self, monkeypatch):
        # Given
        class FakeX11Window:
            pass

        monkeypatch.setattr(
            dock_window_mod.GdkX11,
            "X11Window",
            FakeX11Window,
            raising=False,
        )
        set_struts = MagicMock()
        monkeypatch.setattr(dock_window_mod, "set_dock_struts", set_struts)
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        monitor = SimpleNamespace(get_geometry=lambda: geom)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        gdk_window = FakeX11Window()
        stub = SimpleNamespace(
            config=SimpleNamespace(autohide=False, icon_size=48, pos=Position.BOTTOM),
            theme=SimpleNamespace(bottom_padding=8),
            get_window=lambda: gdk_window,
            get_display=lambda: display,
            get_screen=lambda: MagicMock(),
        )

        # When
        dock_window_mod.DockWindow._set_struts(stub)

        # Then
        set_struts.assert_called_once()

    def test_clear_struts_calls_helper_for_x11(self, monkeypatch):
        # Given
        class FakeX11Window:
            pass

        monkeypatch.setattr(
            dock_window_mod.GdkX11,
            "X11Window",
            FakeX11Window,
            raising=False,
        )
        clear = MagicMock()
        monkeypatch.setattr(dock_window_mod, "clear_struts", clear)
        gdk_window = FakeX11Window()
        stub = SimpleNamespace(get_window=lambda: gdk_window)

        # When
        dock_window_mod.DockWindow._clear_struts(stub)

        # Then
        clear.assert_called_once_with(gdk_window=gdk_window)

    def test_update_input_region_applies_shape_and_caches_rect(self, monkeypatch):
        # Given
        gdk_window = MagicMock()
        monkeypatch.setattr(
            dock_window_mod,
            "compute_layout",
            lambda *args, **kwargs: [SimpleNamespace(x=0.0, scale=1.0)],
        )
        monkeypatch.setattr(
            dock_window_mod,
            "content_bounds",
            lambda **kwargs: (0.0, 120.0),
        )
        stub = SimpleNamespace(
            get_window=lambda: gdk_window,
            model=SimpleNamespace(
                visible_items=lambda: [DockItem(desktop_id="a.desktop")]
            ),
            config=SimpleNamespace(icon_size=48, pos=Position.BOTTOM),
            theme=SimpleNamespace(item_padding=8, h_padding=8, bottom_padding=6),
            autohide=None,
            get_size=lambda: (400, 90),
            _last_input_rect=None,
        )

        # When
        dock_window_mod.DockWindow._update_input_region(stub)
        first_rect = stub._last_input_rect
        dock_window_mod.DockWindow._update_input_region(stub)

        # Then
        assert first_rect is not None
        gdk_window.input_shape_combine_region.assert_called_once()


class TestDockWindowDrawAndHelpers:
    def test_on_draw_invokes_renderer_and_updates_region(self):
        # Given
        stub = SimpleNamespace(
            autohide=None,
            _dnd=None,
            _hover=SimpleNamespace(hovered_item=None),
            renderer=SimpleNamespace(draw=MagicMock()),
            model=MagicMock(),
            config=MagicMock(),
            theme=MagicMock(),
            _main_axis_cursor=lambda: 12.0,
            _update_input_region=MagicMock(),
            _has_active_urgent_glow=lambda: False,
            cursor_x=1.0,
            cursor_y=2.0,
        )

        # When
        result = dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        # Then
        assert result is True
        stub.renderer.draw.assert_called_once()
        stub._update_input_region.assert_called_once()

    def test_on_draw_resets_cursor_when_hidden(self):
        # Given
        stub = SimpleNamespace(
            autohide=SimpleNamespace(
                enabled=True, state=HideState.HIDDEN, hide_offset=0.0, zoom_progress=0.0
            ),
            _dnd=None,
            _hover=SimpleNamespace(hovered_item=None),
            renderer=SimpleNamespace(draw=MagicMock()),
            model=MagicMock(),
            config=MagicMock(),
            theme=MagicMock(),
            _main_axis_cursor=lambda: -1.0,
            _update_input_region=MagicMock(),
            _has_active_urgent_glow=lambda: False,
            cursor_x=25.0,
            cursor_y=33.0,
        )

        # When
        dock_window_mod.DockWindow._on_draw(stub, MagicMock(), MagicMock())

        # Then
        assert stub.cursor_x == -1.0
        assert stub.cursor_y == -1.0

    def test_on_motion_updates_cursor_and_hover(self):
        # Given
        widget = MagicMock()
        stub = SimpleNamespace(
            cursor_x=-1.0,
            cursor_y=-1.0,
            _update_dock_size=MagicMock(),
            _hover=SimpleNamespace(update=MagicMock()),
            _main_axis_cursor=lambda: 12.0,
        )
        event = SimpleNamespace(x=7.0, y=9.0)

        # When
        handled = dock_window_mod.DockWindow._on_motion(stub, widget, event)

        # Then
        assert handled is False
        assert stub.cursor_x == 7.0
        assert stub.cursor_y == 9.0
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

    def test_main_axis_helpers_and_aliases(self):
        # Given
        item = DockItem(desktop_id="a.desktop")
        stub, _ = _make_stub(item=item)
        stub.get_size = lambda: (300, 80)
        stub.config.icon_size = 48
        stub.theme.h_padding = 8
        stub.theme.item_padding = 10
        stub.cursor_x = 40.0
        stub.cursor_y = 20.0
        stub.local_cursor_main = lambda: 123.0
        stub.zoomed_main_offset = lambda layout: 11.0
        stub._main_axis_window_size = lambda: 300

        # Then
        assert dock_window_mod.DockWindow._main_axis_cursor(stub) == 40.0
        assert dock_window_mod.DockWindow._main_axis_window_size(stub) == 300
        assert isinstance(dock_window_mod.DockWindow._base_main_offset(stub), float)
        assert dock_window_mod.DockWindow.local_cursor_x(stub) == 123.0
        assert dock_window_mod.DockWindow.zoomed_x_offset(stub, _layout()) == 11.0

    def test_local_cursor_main_returns_sentinel_when_absent(self):
        # Given
        stub = SimpleNamespace(
            _main_axis_cursor=lambda: -1.0,
            _base_main_offset=lambda: 100.0,
        )

        # When
        value = dock_window_mod.DockWindow.local_cursor_main(stub)

        # Then
        assert value == -1e6

    def test_hit_test_returns_matching_item(self):
        # Given
        items = [DockItem(desktop_id="a.desktop"), DockItem(desktop_id="b.desktop")]
        stub = SimpleNamespace(
            zoomed_main_offset=lambda layout: 0.0,
            model=SimpleNamespace(visible_items=lambda: items),
            config=SimpleNamespace(icon_size=48),
        )
        layout = [SimpleNamespace(x=0.0, scale=1.0), SimpleNamespace(x=60.0, scale=1.0)]

        # When
        item = dock_window_mod.DockWindow.hit_test(stub, main_coord=65.0, layout=layout)

        # Then
        assert item is items[1]

    def test_reposition_and_queue_redraw(self):
        # Given
        drawing_area = MagicMock()
        stub = SimpleNamespace(
            _position_dock=MagicMock(),
            _set_struts=MagicMock(),
            _update_input_region=MagicMock(),
            drawing_area=drawing_area,
        )

        # When
        dock_window_mod.DockWindow.reposition(stub)
        dock_window_mod.DockWindow.queue_redraw(stub)

        # Then
        stub._position_dock.assert_called_once()
        stub._set_struts.assert_called_once()
        stub._update_input_region.assert_called_once()
        assert drawing_area.queue_draw.call_count == 2
