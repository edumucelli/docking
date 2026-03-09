"""Tests for dock placement, monitor selection, and X11 edge integration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.ui.placement as placement_mod
from docking.core.position import Position


def _make_window(**overrides):
    window = SimpleNamespace(
        config=SimpleNamespace(
            icon_size=48,
            zoom_enabled=True,
            zoom_percent=1.2,
            pos=Position.BOTTOM,
            active_display=False,
            autohide=False,
            monitor_index=-1,
        ),
        theme=SimpleNamespace(
            top_padding=4,
            bottom_padding=8,
            urgent_bounce_height=0.5,
            distance_from_edge=0,
        ),
        get_display=MagicMock(),
        get_screen=MagicMock(),
        get_window=MagicMock(),
        get_realized=MagicMock(return_value=True),
        set_size_request=MagicMock(),
        resize=MagicMock(),
        move=MagicMock(),
        drawing_area=SimpleNamespace(queue_draw=MagicMock()),
        update_input_region=MagicMock(),
    )
    for key, value in overrides.items():
        setattr(window, key, value)
    return window


class TestPlacementControllerLifecycle:
    def test_on_realize_initializes_barrier_and_active_display_for_x11(
        self, monkeypatch
    ):
        class FakeX11Display:
            pass

        monkeypatch.setattr(
            placement_mod.GdkX11,
            "X11Display",
            FakeX11Display,
            raising=False,
        )
        screen = SimpleNamespace(connect=MagicMock(side_effect=[51, 52]))
        display = FakeX11Display()
        window = _make_window(
            get_display=lambda: display,
            get_screen=lambda: screen,
            config=SimpleNamespace(active_display=True),
        )
        barrier = MagicMock()
        controller = placement_mod.DockPlacementController(window, barrier=barrier)
        controller.position_dock = MagicMock()
        controller.set_struts = MagicMock()
        controller.start_active_display = MagicMock()

        controller.on_realize()

        barrier.initialize.assert_called_once_with(gdk_display=display)
        controller.start_active_display.assert_called_once()

    def test_on_realize_calls_position_struts_and_input_update(self):
        screen = SimpleNamespace(
            connect=MagicMock(side_effect=[21, 22]), disconnect=MagicMock()
        )
        window = _make_window(get_display=lambda: None, get_screen=lambda: screen)
        controller = placement_mod.DockPlacementController(window)
        controller.position_dock = MagicMock()
        controller.set_struts = MagicMock()
        controller.start_active_display = MagicMock()

        controller.on_realize()

        controller.position_dock.assert_called_once()
        controller.set_struts.assert_called_once()
        window.update_input_region.assert_called_once()
        assert len(controller._screen_signal_handlers) == 2
        controller.start_active_display.assert_not_called()

    def test_on_screen_changed_reattaches_and_schedules_reposition(self):
        screen = SimpleNamespace(
            connect=MagicMock(side_effect=[31, 32]), disconnect=MagicMock()
        )
        window = _make_window(get_screen=lambda: screen)
        controller = placement_mod.DockPlacementController(window)
        controller.schedule_reposition = MagicMock()

        controller.on_screen_changed(MagicMock(), None)

        assert len(controller._screen_signal_handlers) == 2
        controller.schedule_reposition.assert_called_once()

    def test_on_scale_factor_changed_schedules_reposition(self):
        window = _make_window()
        controller = placement_mod.DockPlacementController(window)
        controller.schedule_reposition = MagicMock()

        controller.on_scale_factor_changed()

        controller.schedule_reposition.assert_called_once()

    def test_schedule_reposition_coalesces_until_idle_runs(self, monkeypatch):
        window = _make_window()
        controller = placement_mod.DockPlacementController(window)
        controller.reposition = MagicMock()
        idle_calls: list[object] = []
        monkeypatch.setattr(
            placement_mod.GLib,
            "idle_add",
            lambda cb: idle_calls.append(cb) or 88,
        )

        controller.schedule_reposition()
        controller.schedule_reposition()

        assert controller._geometry_refresh_source == 88
        assert len(idle_calls) == 1

        result = controller.apply_scheduled_reposition()

        assert result is False
        assert controller._geometry_refresh_source == 0
        controller.reposition.assert_called_once()

    def test_apply_scheduled_reposition_uses_latest_monitor_metrics(self):
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1056)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        window = _make_window(get_display=lambda: display)
        controller = placement_mod.DockPlacementController(window)
        controller.update_barrier = MagicMock()
        controller._geometry_refresh_source = 88

        # Simulate a metrics change that happens after scheduling but before apply.
        geom.width = 2560
        work.width = 2560

        controller.apply_scheduled_reposition()

        window.resize.assert_called_with(2560, 93)
        window.move.assert_called_with(0, 987)

    def test_on_destroy_cleans_geometry_refresh_and_screen_handlers(self, monkeypatch):
        screen = SimpleNamespace(disconnect=MagicMock())
        window = _make_window()
        controller = placement_mod.DockPlacementController(window)
        controller._geometry_refresh_source = 91
        controller._screen_signal_handlers = [(screen, 4), (screen, 5)]
        removed: list[int] = []
        monkeypatch.setattr(
            placement_mod.GLib, "source_remove", lambda source: removed.append(source)
        )

        controller.on_destroy()

        assert removed == [91]
        assert controller._geometry_refresh_source == 0
        assert controller._screen_signal_handlers == []
        screen.disconnect.assert_any_call(4)
        screen.disconnect.assert_any_call(5)


class TestPlacementControllerGeometry:
    def test_current_monitor_choice_handles_missing_and_invalid_monitors(self):
        window = _make_window(get_display=lambda: None)
        controller = placement_mod.DockPlacementController(window)
        assert controller.current_monitor_choice() == -1

        zero_display = SimpleNamespace(get_n_monitors=lambda: 0)
        window = _make_window(
            get_display=lambda: zero_display,
            config=SimpleNamespace(monitor_index=-1),
        )
        controller = placement_mod.DockPlacementController(window)
        assert controller.current_monitor_choice() == -1

        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_primary_monitor=lambda: "primary",
            get_monitor=lambda idx: "primary" if idx == 0 else "secondary",
        )
        window = _make_window(
            get_display=lambda: display,
            config=SimpleNamespace(monitor_index=99),
        )
        controller = placement_mod.DockPlacementController(window)
        assert controller.current_monitor_choice() == 0

    def test_current_monitor_choice_returns_selected_monitor(self):
        display = SimpleNamespace(
            get_n_monitors=lambda: 3,
            get_primary_monitor=lambda: "primary",
            get_monitor=lambda idx: f"monitor-{idx}",
        )
        window = _make_window(
            get_display=lambda: display,
            config=SimpleNamespace(monitor_index=2),
        )
        controller = placement_mod.DockPlacementController(window)

        assert controller.current_monitor_choice() == 2

    def test_primary_monitor_index_falls_back_to_zero(self):
        controller = placement_mod.DockPlacementController(
            _make_window(get_display=lambda: None)
        )
        assert controller.primary_monitor_index() == 0

        zero_display = SimpleNamespace(get_n_monitors=lambda: 0)
        controller = placement_mod.DockPlacementController(
            _make_window(get_display=lambda: zero_display)
        )
        assert controller.primary_monitor_index() == 0

    def test_primary_monitor_index_uses_primary_fallbacks(self):
        primary = object()
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_primary_monitor=lambda: None,
            get_monitor=lambda idx: primary if idx == 0 else object(),
        )
        controller = placement_mod.DockPlacementController(
            _make_window(get_display=lambda: display)
        )
        assert controller.primary_monitor_index() == 0

        fallback_display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_primary_monitor=lambda: object(),
            get_monitor=lambda _idx: object(),
        )
        controller = placement_mod.DockPlacementController(
            _make_window(get_display=lambda: fallback_display)
        )
        assert controller.primary_monitor_index() == 0

    def test_get_monitor_menu_choices_skips_missing_monitors(self):
        geom1 = SimpleNamespace(width=1920, height=1080)
        mon1 = SimpleNamespace(get_geometry=lambda: geom1)
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_primary_monitor=lambda: mon1,
            get_monitor=lambda idx: mon1 if idx == 0 else None,
        )
        controller = placement_mod.DockPlacementController(
            _make_window(get_display=lambda: display)
        )

        assert controller.get_monitor_menu_choices() == [
            ("Display 1: 1920x1080 (Primary)", 0)
        ]

    def test_position_dock_horizontal_bottom(self):
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1056)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        window = _make_window(get_display=lambda: display)
        controller = placement_mod.DockPlacementController(window)
        controller.update_barrier = MagicMock()

        controller.position_dock()

        window.set_size_request.assert_called_once()
        window.resize.assert_called_once()
        window.move.assert_called_once()
        controller.update_barrier.assert_called_once()

    def test_position_dock_bottom_keeps_window_on_screen_edge_with_theme_gap(self):
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1056)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        window = _make_window(
            get_display=lambda: display,
            config=SimpleNamespace(
                icon_size=48,
                zoom_enabled=False,
                zoom_percent=1.0,
                pos=Position.BOTTOM,
                active_display=False,
                autohide=False,
                monitor_index=-1,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.0,
                distance_from_edge=6,
            ),
        )
        controller = placement_mod.DockPlacementController(window)
        controller.update_barrier = MagicMock()

        controller.position_dock()

        window.move.assert_called_once_with(0, 1014)

    def test_position_dock_right_keeps_window_on_screen_edge_with_theme_gap(self):
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1000)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        window = _make_window(
            get_display=lambda: display,
            config=SimpleNamespace(
                icon_size=48,
                zoom_enabled=False,
                zoom_percent=1.0,
                pos=Position.RIGHT,
                active_display=False,
                autohide=False,
                monitor_index=-1,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.0,
                distance_from_edge=6,
            ),
        )
        controller = placement_mod.DockPlacementController(window)
        controller.update_barrier = MagicMock()

        controller.position_dock()

        window.move.assert_called_once_with(1854, 24)

    def test_position_dock_uses_selected_monitor_index(self):
        geom_primary = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work_primary = SimpleNamespace(x=0, y=24, width=1920, height=1056)
        geom_secondary = SimpleNamespace(x=1920, y=0, width=1280, height=1024)
        work_secondary = SimpleNamespace(x=1920, y=24, width=1280, height=1000)
        primary = SimpleNamespace(
            get_geometry=lambda: geom_primary, get_workarea=lambda: work_primary
        )
        secondary = SimpleNamespace(
            get_geometry=lambda: geom_secondary, get_workarea=lambda: work_secondary
        )
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_primary_monitor=lambda: primary,
            get_monitor=lambda idx: secondary if idx == 1 else primary,
        )
        window = _make_window(
            get_display=lambda: display,
            config=SimpleNamespace(
                icon_size=48,
                zoom_enabled=True,
                zoom_percent=1.2,
                pos=Position.BOTTOM,
                active_display=False,
                autohide=False,
                monitor_index=1,
            ),
        )
        controller = placement_mod.DockPlacementController(window)
        controller.update_barrier = MagicMock()

        controller.position_dock()

        assert window.move.call_args[0][0] >= 1920

    def test_position_dock_returns_when_no_monitor_is_resolved(self):
        window = _make_window(get_display=lambda: MagicMock())
        controller = placement_mod.DockPlacementController(window)
        controller._resolve_target_monitor = MagicMock(return_value=None)

        controller.position_dock()

        window.move.assert_not_called()
        window.resize.assert_not_called()

    def test_get_monitor_menu_choices_only_for_multiple_monitors(self):
        geom = SimpleNamespace(width=1920, height=1080)
        primary = SimpleNamespace(get_geometry=lambda: geom)
        display = SimpleNamespace(
            get_n_monitors=lambda: 1,
            get_primary_monitor=lambda: primary,
            get_monitor=lambda _idx: primary,
        )
        controller = placement_mod.DockPlacementController(
            _make_window(get_display=lambda: display)
        )

        assert controller.get_monitor_menu_choices() == []

    def test_get_monitor_menu_choices_does_not_duplicate_primary(self):
        geom1 = SimpleNamespace(width=1920, height=1080)
        geom2 = SimpleNamespace(width=2560, height=1440)
        mon1 = SimpleNamespace(get_geometry=lambda: geom1)
        mon2 = SimpleNamespace(get_geometry=lambda: geom2)
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_primary_monitor=lambda: mon1,
            get_monitor=lambda idx: mon1 if idx == 0 else mon2,
        )
        controller = placement_mod.DockPlacementController(
            _make_window(get_display=lambda: display)
        )

        choices = controller.get_monitor_menu_choices()

        labels = [label for label, _ in choices]
        assert labels == ["Display 1: 1920x1080 (Primary)", "Display 2: 2560x1440"]


class TestPlacementControllerStruts:
    def test_set_struts_clears_when_autohide_enabled(self):
        window = _make_window(config=SimpleNamespace(autohide=True))
        controller = placement_mod.DockPlacementController(window)
        controller.clear_struts = MagicMock()

        controller.set_struts()

        controller.clear_struts.assert_called_once()

    def test_set_struts_returns_when_no_window(self):
        window = _make_window(
            config=SimpleNamespace(autohide=False),
            get_window=lambda: None,
        )
        controller = placement_mod.DockPlacementController(window)

        controller.set_struts()

    def test_set_struts_returns_when_target_monitor_is_missing(self, monkeypatch):
        class FakeX11Window:
            pass

        monkeypatch.setattr(
            placement_mod.GdkX11,
            "X11Window",
            FakeX11Window,
            raising=False,
        )
        set_struts = MagicMock()
        monkeypatch.setattr(placement_mod, "set_dock_struts", set_struts)
        window = _make_window(
            config=SimpleNamespace(
                autohide=False,
                icon_size=48,
                pos=Position.BOTTOM,
                active_display=False,
                monitor_index=-1,
            ),
            get_window=lambda: FakeX11Window(),
            get_display=lambda: MagicMock(),
        )
        controller = placement_mod.DockPlacementController(window)
        controller._resolve_target_monitor = MagicMock(return_value=None)

        controller.set_struts()

        set_struts.assert_not_called()

    def test_set_struts_calls_platform_helper_for_x11(self, monkeypatch):
        class FakeX11Window:
            pass

        monkeypatch.setattr(
            placement_mod.GdkX11,
            "X11Window",
            FakeX11Window,
            raising=False,
        )
        set_struts = MagicMock()
        monkeypatch.setattr(placement_mod, "set_dock_struts", set_struts)
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        monitor = SimpleNamespace(get_geometry=lambda: geom)
        display = SimpleNamespace(
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        gdk_window = FakeX11Window()
        window = _make_window(
            config=SimpleNamespace(
                autohide=False,
                icon_size=48,
                pos=Position.BOTTOM,
                active_display=False,
                monitor_index=-1,
            ),
            theme=SimpleNamespace(bottom_padding=8, distance_from_edge=0),
            get_window=lambda: gdk_window,
            get_display=lambda: display,
            get_screen=lambda: MagicMock(),
        )
        controller = placement_mod.DockPlacementController(window)

        controller.set_struts()

        set_struts.assert_called_once()

    def test_set_struts_uses_active_display_monitor_when_enabled(self, monkeypatch):
        class FakeX11Window:
            pass

        monkeypatch.setattr(
            placement_mod.GdkX11,
            "X11Window",
            FakeX11Window,
            raising=False,
        )
        set_struts = MagicMock()
        monkeypatch.setattr(placement_mod, "set_dock_struts", set_struts)
        primary_geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        active_geom = SimpleNamespace(x=1920, y=0, width=2560, height=1440)
        primary = SimpleNamespace(get_geometry=lambda: primary_geom)
        active = SimpleNamespace(get_geometry=lambda: active_geom)
        display = SimpleNamespace(
            get_primary_monitor=lambda: primary,
            get_monitor=lambda _idx: primary,
        )
        gdk_window = FakeX11Window()
        window = _make_window(
            config=SimpleNamespace(
                autohide=False,
                icon_size=48,
                pos=Position.BOTTOM,
                active_display=True,
                monitor_index=-1,
            ),
            theme=SimpleNamespace(bottom_padding=8, distance_from_edge=0),
            get_window=lambda: gdk_window,
            get_display=lambda: display,
            get_screen=lambda: MagicMock(),
        )
        controller = placement_mod.DockPlacementController(window)
        controller._active_monitor = active

        controller.set_struts()

        assert set_struts.call_args.kwargs["monitor_geom"] is active_geom

    def test_clear_struts_calls_helper_for_x11(self, monkeypatch):
        class FakeX11Window:
            pass

        monkeypatch.setattr(
            placement_mod.GdkX11,
            "X11Window",
            FakeX11Window,
            raising=False,
        )
        clear = MagicMock()
        monkeypatch.setattr(placement_mod, "clear_struts", clear)
        gdk_window = FakeX11Window()
        controller = placement_mod.DockPlacementController(
            _make_window(get_window=lambda: gdk_window)
        )

        controller.clear_struts()

        clear.assert_called_once_with(gdk_window=gdk_window)

    def test_update_barrier_handles_supported_states(self):
        barrier = MagicMock(supported=False)
        controller = placement_mod.DockPlacementController(
            _make_window(), barrier=barrier
        )

        controller.update_barrier()

        barrier.destroy.assert_not_called()
        barrier.update.assert_not_called()

        barrier = MagicMock(supported=True)
        window = _make_window(config=SimpleNamespace(autohide=False))
        controller = placement_mod.DockPlacementController(window, barrier=barrier)
        controller.update_barrier()
        barrier.destroy.assert_called_once()

    def test_update_barrier_destroys_when_monitor_missing(self):
        barrier = MagicMock(supported=True)
        window = _make_window(
            config=SimpleNamespace(autohide=True, pos=Position.BOTTOM)
        )
        controller = placement_mod.DockPlacementController(window, barrier=barrier)
        controller._resolve_target_monitor = MagicMock(return_value=None)

        controller.update_barrier()

        barrier.destroy.assert_called_once()

    def test_update_barrier_updates_monitor_geometry(self):
        barrier = MagicMock(supported=True)
        geom = SimpleNamespace(x=100, y=50, width=1280, height=720)
        monitor = SimpleNamespace(get_geometry=lambda: geom)
        window = _make_window(config=SimpleNamespace(autohide=True, pos=Position.RIGHT))
        controller = placement_mod.DockPlacementController(window, barrier=barrier)
        controller._resolve_target_monitor = MagicMock(return_value=monitor)

        controller.update_barrier()

        barrier.update.assert_called_once_with(
            position=Position.RIGHT,
            monitor_x=100,
            monitor_y=50,
            monitor_w=1280,
            monitor_h=720,
        )

    def test_update_struts_refreshes_barrier_and_struts(self):
        controller = placement_mod.DockPlacementController(_make_window())
        controller.set_struts = MagicMock()
        controller.update_barrier = MagicMock()

        controller.update_struts()

        controller.set_struts.assert_called_once()
        controller.update_barrier.assert_called_once()

    def test_start_and_stop_active_display_manage_timer(self, monkeypatch):
        added: list[tuple[int, object]] = []
        removed: list[int] = []
        monkeypatch.setattr(
            placement_mod.GLib,
            "timeout_add_seconds",
            lambda delay, callback: added.append((delay, callback)) or 77,
        )
        monkeypatch.setattr(
            placement_mod.GLib, "source_remove", lambda source: removed.append(source)
        )
        controller = placement_mod.DockPlacementController(_make_window())

        controller.start_active_display()
        controller.start_active_display()
        controller.stop_active_display()

        assert added == [(2, controller._poll_active_display)]
        assert removed == [77]
        assert controller._active_display_timer == 0

    def test_poll_active_display_handles_missing_cursor_services(self):
        controller = placement_mod.DockPlacementController(
            _make_window(get_display=lambda: None)
        )
        assert controller._poll_active_display() is True

        display = SimpleNamespace(get_default_seat=lambda: None)
        controller = placement_mod.DockPlacementController(
            _make_window(get_display=lambda: display)
        )
        assert controller._poll_active_display() is True

        seat = SimpleNamespace(get_pointer=lambda: None)
        display = SimpleNamespace(get_default_seat=lambda: seat)
        controller = placement_mod.DockPlacementController(
            _make_window(get_display=lambda: display)
        )
        assert controller._poll_active_display() is True

    def test_poll_active_display_repositions_when_monitor_changes(self):
        pointer = SimpleNamespace(get_position=lambda: (None, 400, 100))
        seat = SimpleNamespace(get_pointer=lambda: pointer)
        monitor = object()
        display = SimpleNamespace(
            get_default_seat=lambda: seat,
            get_monitor_at_point=lambda x, y: monitor,
        )
        controller = placement_mod.DockPlacementController(
            _make_window(get_display=lambda: display)
        )
        controller.reposition = MagicMock()

        assert controller._poll_active_display() is True

        assert controller._active_monitor is monitor
        controller.reposition.assert_called_once()

    def test_resolve_target_monitor_uses_active_display_and_fallbacks(self):
        controller = placement_mod.DockPlacementController(
            _make_window(config=SimpleNamespace(active_display=True, monitor_index=-1))
        )
        active_monitor = object()
        controller._active_monitor = active_monitor
        assert controller._resolve_target_monitor(display=MagicMock()) is active_monitor

        primary = object()
        no_get_n = SimpleNamespace(
            get_primary_monitor=lambda: None,
            get_monitor=lambda idx: primary if idx == 0 else None,
        )
        controller = placement_mod.DockPlacementController(
            _make_window(config=SimpleNamespace(active_display=False, monitor_index=-1))
        )
        assert controller._resolve_target_monitor(display=no_get_n) is primary

        empty_display = SimpleNamespace(get_n_monitors=lambda: 0)
        assert controller._resolve_target_monitor(display=empty_display) is None

        selected = object()
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_monitor=lambda idx: selected if idx == 1 else None,
            get_primary_monitor=lambda: None,
        )
        controller = placement_mod.DockPlacementController(
            _make_window(config=SimpleNamespace(active_display=False, monitor_index=1))
        )
        assert controller._resolve_target_monitor(display=display) is selected

        fallback = object()
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_monitor=lambda idx: fallback if idx == 0 else None,
            get_primary_monitor=lambda: None,
        )
        controller = placement_mod.DockPlacementController(
            _make_window(config=SimpleNamespace(active_display=False, monitor_index=9))
        )
        assert controller._resolve_target_monitor(display=display) is fallback

    def test_reposition_updates_input_region_and_redraw(self):
        window = _make_window()
        controller = placement_mod.DockPlacementController(window)
        controller.position_dock = MagicMock()
        controller.set_struts = MagicMock()

        controller.reposition()

        controller.position_dock.assert_called_once()
        controller.set_struts.assert_called_once()
        window.update_input_region.assert_called_once()
        window.drawing_area.queue_draw.assert_called_once()
