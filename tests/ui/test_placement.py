"""Tests for dock placement, monitor selection, and X11 edge integration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import docking.ui.placement as placement_mod
from docking.core.position import Position


def _make_window(**overrides):
    surface_service = MagicMock()
    window = SimpleNamespace(
        config=SimpleNamespace(
            icon_size=48,
            zoom_enabled=True,
            zoom_percent=1.2,
            pos=Position.BOTTOM,
            active_display=False,
            hide_mode="none",
            monitor_index=-1,
            additional_distance_from_edge=0,
            pressure_reveal_enabled=False,
            pressure_threshold=50,
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
        get_scale_factor=MagicMock(return_value=1),
        get_realized=MagicMock(return_value=True),
        set_size_request=MagicMock(),
        resize=MagicMock(),
        move=MagicMock(),
        drawing_area=SimpleNamespace(queue_draw=MagicMock()),
        update_input_region=MagicMock(),
        surface_service=surface_service,
    )
    for key, value in overrides.items():
        setattr(window, key, value)

    def _position_or_anchor(request):
        window.set_size_request(request.size.width, request.size.height)
        window.resize(request.size.width, request.size.height)
        window.move(request.x, request.y)

    surface_service.position_or_anchor.side_effect = _position_or_anchor
    return window


def _make_controller(window):
    return placement_mod.DockPlacementController(
        window,
        surface_service=window.surface_service,
    )


class TestPlacementControllerLifecycle:
    def test_on_realize_initializes_surface_and_active_display(self):
        screen = SimpleNamespace(connect=MagicMock(side_effect=[51, 52]))
        window = _make_window(
            get_display=lambda: object(),
            get_screen=lambda: screen,
            config=SimpleNamespace(
                active_display=True,
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            ),
        )
        controller = _make_controller(window)
        controller.position_dock = MagicMock()
        controller.set_struts = MagicMock()
        controller.start_active_display = MagicMock()

        controller.on_realize()

        window.surface_service.on_realize.assert_called_once_with(window)
        controller.start_active_display.assert_called_once()

    def test_on_realize_calls_position_struts_and_input_update(self):
        screen = SimpleNamespace(
            connect=MagicMock(side_effect=[21, 22]), disconnect=MagicMock()
        )
        window = _make_window(get_display=lambda: None, get_screen=lambda: screen)
        controller = _make_controller(window)
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
        controller = _make_controller(window)
        controller.schedule_reposition = MagicMock()

        controller.on_screen_changed(MagicMock(), None)

        assert len(controller._screen_signal_handlers) == 2
        controller.schedule_reposition.assert_called_once()

    def test_on_scale_factor_changed_schedules_reposition(self):
        window = _make_window()
        controller = _make_controller(window)
        controller.schedule_reposition = MagicMock()

        controller.on_scale_factor_changed()

        controller.schedule_reposition.assert_called_once()

    def test_schedule_reposition_coalesces_until_idle_runs(self, monkeypatch):
        window = _make_window()
        controller = _make_controller(window)
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
            get_n_monitors=lambda: 1,
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        window = _make_window(get_display=lambda: display)
        controller = _make_controller(window)
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
        controller = _make_controller(window)
        controller._geometry_refresh_source = 91
        controller._active_display_timer = 92
        controller._screen_signal_handlers = [(screen, 4), (screen, 5)]
        removed: list[int] = []
        monkeypatch.setattr(
            placement_mod.GLib, "source_remove", lambda source: removed.append(source)
        )

        controller.on_destroy()

        assert removed == [91, 92]
        assert controller._geometry_refresh_source == 0
        assert controller._active_display_timer == 0
        assert controller._screen_signal_handlers == []
        screen.disconnect.assert_any_call(4)
        screen.disconnect.assert_any_call(5)


class TestPlacementControllerGeometry:
    def test_current_monitor_choice_handles_missing_and_invalid_monitors(self):
        window = _make_window(get_display=lambda: None)
        controller = _make_controller(window)
        assert controller.current_monitor_choice() == -1

        zero_display = SimpleNamespace(get_n_monitors=lambda: 0)
        window = _make_window(
            get_display=lambda: zero_display,
            config=SimpleNamespace(
                monitor_index=-1, pressure_reveal_enabled=False, pressure_threshold=50
            ),
        )
        controller = _make_controller(window)
        assert controller.current_monitor_choice() == -1

        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_primary_monitor=lambda: "primary",
            get_monitor=lambda idx: "primary" if idx == 0 else "secondary",
        )
        window = _make_window(
            get_display=lambda: display,
            config=SimpleNamespace(
                monitor_index=99, pressure_reveal_enabled=False, pressure_threshold=50
            ),
        )
        controller = _make_controller(window)
        assert controller.current_monitor_choice() == 0

    def test_current_monitor_choice_returns_selected_monitor(self):
        display = SimpleNamespace(
            get_n_monitors=lambda: 3,
            get_primary_monitor=lambda: "primary",
            get_monitor=lambda idx: f"monitor-{idx}",
        )
        window = _make_window(
            get_display=lambda: display,
            config=SimpleNamespace(
                monitor_index=2, pressure_reveal_enabled=False, pressure_threshold=50
            ),
        )
        controller = _make_controller(window)

        assert controller.current_monitor_choice() == 2

    def test_primary_monitor_index_falls_back_to_zero(self):
        controller = _make_controller(_make_window(get_display=lambda: None))
        assert controller.primary_monitor_index() == 0

        zero_display = SimpleNamespace(get_n_monitors=lambda: 0)
        controller = _make_controller(_make_window(get_display=lambda: zero_display))
        assert controller.primary_monitor_index() == 0

    def test_primary_monitor_index_uses_primary_fallbacks(self):
        primary = object()
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_primary_monitor=lambda: None,
            get_monitor=lambda idx: primary if idx == 0 else object(),
        )
        controller = _make_controller(_make_window(get_display=lambda: display))
        assert controller.primary_monitor_index() == 0

        fallback_display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_primary_monitor=lambda: object(),
            get_monitor=lambda _idx: object(),
        )
        controller = _make_controller(
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
        controller = _make_controller(_make_window(get_display=lambda: display))

        assert controller.get_monitor_menu_choices() == [
            ("Display 1: 1920x1080 (Primary)", 0)
        ]

    def test_position_dock_horizontal_bottom(self):
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1056)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_n_monitors=lambda: 1,
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        window = _make_window(get_display=lambda: display)
        controller = _make_controller(window)
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
            get_n_monitors=lambda: 1,
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
                hide_mode="none",
                monitor_index=-1,
                additional_distance_from_edge=0,
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.0,
                distance_from_edge=6,
            ),
        )
        controller = _make_controller(window)
        controller.update_barrier = MagicMock()

        controller.position_dock()

        window.move.assert_called_once_with(0, 1014)

    def test_position_dock_right_keeps_window_on_screen_edge_with_theme_gap(self):
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=24, width=1920, height=1000)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_n_monitors=lambda: 1,
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
                hide_mode="none",
                monitor_index=-1,
                additional_distance_from_edge=0,
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            ),
            theme=SimpleNamespace(
                top_padding=4,
                bottom_padding=8,
                urgent_bounce_height=0.0,
                distance_from_edge=6,
            ),
        )
        controller = _make_controller(window)
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
                hide_mode="none",
                monitor_index=1,
                additional_distance_from_edge=0,
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            ),
        )
        controller = _make_controller(window)
        controller.update_barrier = MagicMock()

        controller.position_dock()

        assert window.move.call_args[0][0] >= 1920

    def test_position_dock_returns_when_no_monitor_is_resolved(self):
        window = _make_window(get_display=lambda: MagicMock())
        controller = _make_controller(window)
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
        controller = _make_controller(_make_window(get_display=lambda: display))

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
        controller = _make_controller(_make_window(get_display=lambda: display))

        choices = controller.get_monitor_menu_choices()

        labels = [label for label, _ in choices]
        assert labels == ["Display 1: 1920x1080 (Primary)", "Display 2: 2560x1440"]


class TestPlacementControllerStruts:
    def test_set_struts_clears_when_autohide_enabled(self):
        window = _make_window(
            config=SimpleNamespace(
                hide_mode="autohide",
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            )
        )
        controller = _make_controller(window)
        controller.clear_struts = MagicMock()

        controller.set_struts()

        controller.clear_struts.assert_called_once()

    def test_set_struts_returns_when_target_monitor_is_missing(self):
        window = _make_window(
            config=SimpleNamespace(
                hide_mode="none",
                icon_size=48,
                pos=Position.BOTTOM,
                active_display=False,
                monitor_index=-1,
                additional_distance_from_edge=0,
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            )
        )
        controller = _make_controller(window)
        controller._resolve_target_monitor = MagicMock(return_value=None)

        controller.set_struts()

        window.surface_service.set_reservation.assert_not_called()

    def test_set_struts_calls_surface_reservation(self):
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        display = SimpleNamespace(
            get_n_monitors=lambda: 1,
            get_primary_monitor=lambda: monitor,
            get_monitor=lambda _idx: monitor,
        )
        window = _make_window(
            config=SimpleNamespace(
                hide_mode="none",
                icon_size=48,
                pos=Position.BOTTOM,
                active_display=False,
                monitor_index=-1,
                additional_distance_from_edge=0,
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            ),
            theme=SimpleNamespace(bottom_padding=8, distance_from_edge=0),
            get_display=lambda: display,
        )
        controller = _make_controller(window)

        controller.set_struts()

        request = window.surface_service.set_reservation.call_args.args[0]
        assert request.position == Position.BOTTOM
        assert request.thickness == 56
        assert request.monitor.geometry == placement_mod.Rect(
            x=0,
            y=0,
            width=1920,
            height=1080,
        )

    def test_set_struts_uses_active_display_monitor_when_enabled(self):
        primary_geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        active_geom = SimpleNamespace(x=1920, y=0, width=2560, height=1440)
        primary_work = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        active_work = SimpleNamespace(x=1920, y=0, width=2560, height=1440)
        primary = SimpleNamespace(
            get_geometry=lambda: primary_geom,
            get_workarea=lambda: primary_work,
        )
        active = SimpleNamespace(
            get_geometry=lambda: active_geom,
            get_workarea=lambda: active_work,
        )
        display = SimpleNamespace(
            get_n_monitors=lambda: 1,
            get_primary_monitor=lambda: primary,
            get_monitor=lambda _idx: primary,
        )
        window = _make_window(
            config=SimpleNamespace(
                hide_mode="none",
                icon_size=48,
                pos=Position.BOTTOM,
                active_display=True,
                monitor_index=-1,
                additional_distance_from_edge=0,
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            ),
            theme=SimpleNamespace(bottom_padding=8, distance_from_edge=0),
            get_display=lambda: display,
        )
        controller = _make_controller(window)
        controller._active_monitor = active

        controller.set_struts()

        request = window.surface_service.set_reservation.call_args.args[0]
        assert request.monitor.geometry == placement_mod.Rect(
            x=1920,
            y=0,
            width=2560,
            height=1440,
        )

    def test_clear_struts_calls_surface_service(self):
        window = _make_window()
        controller = _make_controller(window)

        controller.clear_struts()

        window.surface_service.clear_reservation.assert_called_once_with()

    def test_update_barrier_handles_supported_states(self):
        window = _make_window(
            config=SimpleNamespace(
                hide_mode="none", pressure_reveal_enabled=False, pressure_threshold=50
            )
        )
        controller = _make_controller(window)

        controller.update_barrier()

        window.surface_service.update_pointer_barrier.assert_called_once_with(
            monitor=None,
            position=Position.BOTTOM,
            enabled=False,
        )

    def test_update_barrier_destroys_when_monitor_missing(self):
        window = _make_window(
            config=SimpleNamespace(
                hide_mode="autohide",
                pos=Position.BOTTOM,
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            )
        )
        controller = _make_controller(window)
        controller._resolve_target_monitor = MagicMock(return_value=None)

        controller.update_barrier()

        window.surface_service.update_pointer_barrier.assert_called_once_with(
            monitor=None,
            position=Position.BOTTOM,
            enabled=False,
        )

    def test_update_barrier_updates_monitor_geometry(self):
        geom = SimpleNamespace(x=100, y=50, width=1280, height=720)
        work = SimpleNamespace(x=100, y=50, width=1280, height=720)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        window = _make_window(
            config=SimpleNamespace(
                hide_mode="autohide",
                pos=Position.RIGHT,
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            ),
            get_scale_factor=lambda: 1,
        )
        controller = _make_controller(window)
        controller._resolve_target_monitor = MagicMock(return_value=monitor)

        controller.update_barrier()

        kwargs = window.surface_service.update_pointer_barrier.call_args.kwargs
        assert kwargs["position"] == Position.RIGHT
        assert kwargs["enabled"] is True
        assert kwargs["monitor"].geometry == placement_mod.Rect(
            x=100,
            y=50,
            width=1280,
            height=720,
        )
        assert kwargs["monitor"].scale == 1

    def test_update_barrier_delivers_scale_to_surface_service(self):
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        window = _make_window(
            config=SimpleNamespace(
                hide_mode="autohide",
                pos=Position.BOTTOM,
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            ),
            get_scale_factor=lambda: 2,
        )
        controller = _make_controller(window)
        controller._resolve_target_monitor = MagicMock(return_value=monitor)

        controller.update_barrier()

        kwargs = window.surface_service.update_pointer_barrier.call_args.kwargs
        assert kwargs["monitor"].scale == 2

    @pytest.mark.parametrize(
        "scale",
        [1, 2, 3],
    )
    def test_update_barrier_forwards_display_scale_factor(self, scale):
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        window = _make_window(
            config=SimpleNamespace(
                hide_mode="autohide",
                pos=Position.BOTTOM,
                pressure_reveal_enabled=False,
                pressure_threshold=50,
            ),
            get_scale_factor=lambda: scale,
        )
        controller = _make_controller(window)
        controller._resolve_target_monitor = MagicMock(return_value=monitor)

        controller.update_barrier()

        kwargs = window.surface_service.update_pointer_barrier.call_args.kwargs
        assert kwargs["monitor"].scale == scale

    def test_update_barrier_enables_pressure_callback_when_configured(self):
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        window = _make_window(
            config=SimpleNamespace(
                hide_mode="autohide",
                pos=Position.BOTTOM,
                pressure_reveal_enabled=True,
                pressure_threshold=25,
            ),
            autohide=SimpleNamespace(on_mouse_enter=MagicMock()),
        )
        controller = _make_controller(window)
        controller._resolve_target_monitor = MagicMock(return_value=monitor)

        controller.update_barrier()

        kwargs = window.surface_service.update_pointer_barrier.call_args.kwargs
        assert callable(kwargs["pressure_callback"])
        kwargs["pressure_callback"]()
        window.autohide.on_mouse_enter.assert_called_once_with()
        assert kwargs["pressure_threshold"] == 25

    def test_update_barrier_disables_pressure_callback_when_not_configured(self):
        geom = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        work = SimpleNamespace(x=0, y=0, width=1920, height=1080)
        monitor = SimpleNamespace(get_geometry=lambda: geom, get_workarea=lambda: work)
        window = _make_window(
            config=SimpleNamespace(
                hide_mode="autohide",
                pos=Position.BOTTOM,
                pressure_reveal_enabled=False,
                pressure_threshold=25,
            )
        )
        controller = _make_controller(window)
        controller._resolve_target_monitor = MagicMock(return_value=monitor)

        controller.update_barrier()

        kwargs = window.surface_service.update_pointer_barrier.call_args.kwargs
        assert kwargs["pressure_callback"] is None
        assert kwargs["pressure_threshold"] == 25

    def test_barrier_pressure_reveals_autohide(self):
        controller = _make_controller(
            _make_window(autohide=SimpleNamespace(on_mouse_enter=MagicMock()))
        )

        controller._on_barrier_pressure()

        controller._window.autohide.on_mouse_enter.assert_called_once_with()

    def test_barrier_pressure_noops_without_autohide(self):
        controller = _make_controller(_make_window(autohide=None))

        controller._on_barrier_pressure()

    def test_update_struts_refreshes_barrier_and_struts(self):
        controller = _make_controller(_make_window())
        controller.set_struts = MagicMock()
        controller.update_barrier = MagicMock()

        controller.update_struts()

        controller.set_struts.assert_called_once()
        controller.update_barrier.assert_called_once()

    def test_refresh_pressure_handler_updates_barrier(self):
        controller = _make_controller(_make_window())
        controller.update_barrier = MagicMock()

        controller.refresh_pressure_handler()

        controller.update_barrier.assert_called_once_with()

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
        controller = _make_controller(_make_window())

        controller.start_active_display()
        controller.start_active_display()
        controller.stop_active_display()

        assert added == [(2, controller._poll_active_display)]
        assert removed == [77]
        assert controller._active_display_timer == 0

    def test_poll_active_display_handles_missing_cursor_services(self):
        controller = _make_controller(_make_window(get_display=lambda: None))
        assert controller._poll_active_display() is True

        display = SimpleNamespace(
            get_default_seat=lambda: None, get_n_monitors=lambda: 0
        )
        controller = _make_controller(_make_window(get_display=lambda: display))
        assert controller._poll_active_display() is True

        seat = SimpleNamespace(get_pointer=lambda: None)
        display = SimpleNamespace(
            get_default_seat=lambda: seat, get_n_monitors=lambda: 0
        )
        controller = _make_controller(_make_window(get_display=lambda: display))
        assert controller._poll_active_display() is True

    def test_poll_active_display_repositions_when_monitor_changes(self):
        pointer = SimpleNamespace(get_position=lambda: (None, 400, 100))
        seat = SimpleNamespace(get_pointer=lambda: pointer)
        monitor = SimpleNamespace(
            get_geometry=lambda: SimpleNamespace(x=0, y=0, width=1920, height=1080)
        )
        display = SimpleNamespace(
            get_n_monitors=lambda: 1,
            get_monitor=lambda idx: monitor if idx == 0 else None,
            get_default_seat=lambda: seat,
            get_monitor_at_point=lambda x, y: monitor,
        )
        controller = _make_controller(_make_window(get_display=lambda: display))
        controller.reposition = MagicMock()

        assert controller._poll_active_display() is True

        assert controller._active_monitor is monitor
        controller.reposition.assert_called_once()

    def test_resolve_target_monitor_uses_active_display_and_fallbacks(self):
        controller = _make_controller(
            _make_window(
                config=SimpleNamespace(
                    active_display=True,
                    monitor_index=-1,
                    pressure_reveal_enabled=False,
                    pressure_threshold=50,
                )
            )
        )
        active_monitor = object()
        controller._active_monitor = active_monitor
        assert controller._resolve_target_monitor(display=MagicMock()) is active_monitor

        primary = object()
        no_get_n = SimpleNamespace(
            get_n_monitors=lambda: 1,
            get_primary_monitor=lambda: None,
            get_monitor=lambda idx: primary if idx == 0 else None,
        )
        controller = _make_controller(
            _make_window(
                config=SimpleNamespace(
                    active_display=False,
                    monitor_index=-1,
                    pressure_reveal_enabled=False,
                    pressure_threshold=50,
                )
            )
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
        controller = _make_controller(
            _make_window(
                config=SimpleNamespace(
                    active_display=False,
                    monitor_index=1,
                    pressure_reveal_enabled=False,
                    pressure_threshold=50,
                )
            )
        )
        assert controller._resolve_target_monitor(display=display) is selected

        fallback = object()
        display = SimpleNamespace(
            get_n_monitors=lambda: 2,
            get_monitor=lambda idx: fallback if idx == 0 else None,
            get_primary_monitor=lambda: None,
        )
        controller = _make_controller(
            _make_window(
                config=SimpleNamespace(
                    active_display=False,
                    monitor_index=9,
                    pressure_reveal_enabled=False,
                    pressure_threshold=50,
                )
            )
        )
        assert controller._resolve_target_monitor(display=display) is fallback

    def test_reposition_updates_input_region_and_redraw(self):
        window = _make_window()
        controller = _make_controller(window)
        controller.position_dock = MagicMock()
        controller.set_struts = MagicMock()

        controller.reposition()

        controller.position_dock.assert_called_once()
        controller.set_struts.assert_called_once()
        window.update_input_region.assert_called_once()
        window.drawing_area.queue_draw.assert_called_once()
