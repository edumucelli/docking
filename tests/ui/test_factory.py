"""Tests for UI graph assembly in docking.ui.factory."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

import docking.ui.factory as factory_mod


def _window():
    window = MagicMock()
    window.autohide = MagicMock()
    window.geometry = MagicMock()
    return window


def _patch_ui_components(monkeypatch):
    components = SimpleNamespace(
        about=MagicMock(),
        settings=MagicMock(),
        diagnostics=MagicMock(),
        update_checker=MagicMock(),
        startup_popups=MagicMock(),
        new_year=MagicMock(),
        startup_tips=MagicMock(),
        runtime=MagicMock(),
        folder_stack=MagicMock(),
        dnd=MagicMock(),
        settings_actions=MagicMock(),
        menu=MagicMock(),
        interactions=MagicMock(),
        input_controller=MagicMock(),
        search=MagicMock(),
    )
    monkeypatch.setattr(
        factory_mod,
        "AboutDialogController",
        MagicMock(return_value=components.about),
    )
    monkeypatch.setattr(
        factory_mod,
        "UpdateCheckController",
        MagicMock(return_value=components.update_checker),
    )
    monkeypatch.setattr(
        factory_mod,
        "StartupPopupCoordinator",
        MagicMock(return_value=components.startup_popups),
    )
    monkeypatch.setattr(
        factory_mod,
        "NewYearGreetingController",
        MagicMock(return_value=components.new_year),
    )
    monkeypatch.setattr(
        factory_mod,
        "StartupTipsController",
        MagicMock(return_value=components.startup_tips),
    )
    monkeypatch.setattr(
        factory_mod,
        "DockRuntime",
        MagicMock(return_value=components.runtime),
    )
    monkeypatch.setattr(
        factory_mod,
        "GlobalSearchController",
        MagicMock(return_value=components.search),
    )
    monkeypatch.setattr(
        factory_mod,
        "SettingsWindowController",
        MagicMock(return_value=components.settings),
    )
    monkeypatch.setattr(
        factory_mod,
        "DiagnosticsDialogController",
        MagicMock(return_value=components.diagnostics),
    )
    monkeypatch.setattr(
        factory_mod,
        "FolderStackController",
        MagicMock(return_value=components.folder_stack),
    )
    monkeypatch.setattr(
        factory_mod,
        "DnDHandler",
        MagicMock(return_value=components.dnd),
    )
    monkeypatch.setattr(
        factory_mod,
        "SettingsActions",
        MagicMock(return_value=components.settings_actions),
    )
    monkeypatch.setattr(
        factory_mod,
        "MenuHandler",
        MagicMock(return_value=components.menu),
    )
    monkeypatch.setattr(
        factory_mod,
        "DockInteractions",
        MagicMock(return_value=components.interactions),
    )
    monkeypatch.setattr(
        factory_mod,
        "DockInputController",
        MagicMock(return_value=components.input_controller),
    )
    return components


class TestBuildDockWindow:
    def test_dock_ui_stop_attempts_every_component_after_failures(self):
        order = []
        startup_popups = MagicMock()
        settings = MagicMock()
        search = MagicMock()
        input_controller = MagicMock()

        def stop(name, *, fail=False):
            order.append(name)
            if fail:
                raise RuntimeError(f"{name} failed")

        startup_popups.stop.side_effect = lambda: stop("startup", fail=True)
        settings.close.side_effect = lambda: stop("settings", fail=True)
        search.stop.side_effect = lambda: stop("search")
        input_controller.stop.side_effect = lambda: stop("input")
        ui = factory_mod.DockUi(
            window=MagicMock(),
            startup_popups=startup_popups,
            input_controller=input_controller,
            search=search,
            settings=settings,
        )

        with pytest.raises(RuntimeError, match="settings failed"):
            ui.stop()

        assert order == ["startup", "settings", "search", "input"]

    def test_build_dock_window_constructs_window_and_starts_dodge_monitor(
        self, monkeypatch
    ):
        config = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        theme = MagicMock()
        tracker = MagicMock()
        application_registry = MagicMock()
        application_launcher = MagicMock()
        icon_loader = MagicMock()
        target_service = MagicMock(icon_loader=icon_loader)
        recent_applications = MagicMock()
        preview_service = MagicMock()
        surface_service = MagicMock()
        visibility_service = MagicMock()
        session_backend = MagicMock()
        window = _window()
        dodge_monitor = MagicMock()

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        components = _patch_ui_components(monkeypatch)
        visibility_service.create_monitor.return_value = dodge_monitor

        result = factory_mod.build_dock_window(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            preview_service=preview_service,
            surface_service=surface_service,
            visibility_service=visibility_service,
            application_registry=application_registry,
            session_backend=session_backend,
            application_launcher=application_launcher,
            icon_loader=icon_loader,
            target_service=target_service,
            recent_applications=recent_applications,
        )

        assert result.window is window
        assert result.startup_popups is components.startup_popups
        assert result.input_controller is components.input_controller
        assert result.search is components.search
        assert result.settings is components.settings
        factory_mod.DockWindow.assert_called_once_with(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            preview_service=preview_service,
            surface_service=surface_service,
            session_backend=session_backend,
        )
        kwargs = visibility_service.create_monitor.call_args.kwargs
        assert callable(kwargs["get_dock_rect"])
        assert kwargs["on_change"] is window.autohide.set_window_should_hide
        dodge_monitor.start.assert_called_once_with()
        assert window.dodge_monitor is dodge_monitor
        factory_mod.UpdateCheckController.assert_called_once_with(
            window=window,
            config=config,
        )
        factory_mod.DockRuntime.assert_called_once_with(
            window,
            update_checker=components.update_checker,
        )
        factory_mod.GlobalSearchController.assert_called_once_with(
            config=config,
            model=model,
            windows=tracker,
            preview_service=preview_service,
            application_registry=application_registry,
            application_launcher=application_launcher,
            icon_loader=icon_loader,
            target_service=target_service,
        )
        factory_mod.FolderStackController.assert_called_once_with(
            config=config,
            runtime=components.runtime,
            dock_window=window,
            target_service=target_service,
        )
        factory_mod.DnDHandler.assert_called_once_with(
            drawing_area=window.drawing_area,
            window=window,
            model=model,
            config=config,
            renderer=renderer,
            theme=theme,
            geometry_builder=window.geometry,
            folder_stack=components.folder_stack,
            application_registry=application_registry,
            application_launcher=application_launcher,
            icon_loader=icon_loader,
            target_service=target_service,
        )
        factory_mod.SettingsActions.assert_called_once_with(
            runtime=components.runtime,
            dnd=components.dnd,
            model=model,
            search=components.search,
        )
        factory_mod.SettingsWindowController.assert_called_once_with(
            parent=window,
            actions=components.settings_actions,
            model=model,
            config=config,
            recent_applications=recent_applications,
        )
        factory_mod.MenuHandler.assert_called_once_with(
            about=components.about,
            settings=components.settings,
            diagnostics=components.diagnostics,
            runtime=components.runtime,
            model=model,
            config=config,
            window_tracker=tracker,
            preview_service=preview_service,
            folder_stack=components.folder_stack,
            dock_window=window,
            search=components.search,
            application_registry=application_registry,
            application_launcher=application_launcher,
            target_service=target_service,
        )
        factory_mod.DockInteractions.assert_called_once_with(
            menu=components.menu,
            folder_stack=components.folder_stack,
        )
        factory_mod.DockInputController.assert_called_once_with(
            window=window,
            interactions=components.interactions,
            dnd=components.dnd,
            application_launcher=application_launcher,
            target_service=target_service,
        )
        assert (
            factory_mod.FolderStackController.call_args.kwargs["target_service"]
            is target_service
        )
        assert target_service.icon_loader is icon_loader
        assert (
            factory_mod.DnDHandler.call_args.kwargs["application_registry"]
            is application_registry
        )
        assert (
            factory_mod.DnDHandler.call_args.kwargs["application_launcher"]
            is application_launcher
        )
        assert factory_mod.DnDHandler.call_args.kwargs["icon_loader"] is icon_loader
        assert (
            factory_mod.DnDHandler.call_args.kwargs["target_service"] is target_service
        )
        assert (
            factory_mod.MenuHandler.call_args.kwargs["application_launcher"]
            is application_launcher
        )
        assert (
            factory_mod.MenuHandler.call_args.kwargs["target_service"] is target_service
        )
        assert (
            factory_mod.DockInputController.call_args.kwargs["application_launcher"]
            is application_launcher
        )
        assert (
            factory_mod.DockInputController.call_args.kwargs["target_service"]
            is target_service
        )
        factory_mod.StartupPopupCoordinator.assert_called_once_with()
        factory_mod.NewYearGreetingController.assert_called_once_with(window=window)
        factory_mod.StartupTipsController.assert_called_once_with(
            window=window,
            config=config,
        )
        components.startup_popups.register.assert_any_call(components.new_year)
        components.startup_popups.register.assert_any_call(components.update_checker)
        components.startup_popups.register.assert_any_call(components.startup_tips)
        components.input_controller.start.assert_not_called()
        components.search.start.assert_not_called()
        components.update_checker.start.assert_not_called()
        components.startup_popups.start.assert_not_called()

        result.start()
        components.input_controller.start.assert_called_once_with()
        components.search.start.assert_called_once_with()
        components.startup_popups.start.assert_called_once_with()
        components.update_checker.start.assert_not_called()

        result.stop()
        components.startup_popups.stop.assert_called_once_with()
        components.settings.close.assert_called_once_with()
        components.search.stop.assert_called_once_with()
        components.update_checker.stop.assert_not_called()
        components.input_controller.stop.assert_called_once_with()

    def test_build_dock_window_allows_unsupported_visibility_service(self, monkeypatch):
        config = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        theme = MagicMock()
        tracker = MagicMock()
        application_registry = MagicMock()
        application_launcher = MagicMock()
        icon_loader = MagicMock()
        target_service = MagicMock()
        recent_applications = MagicMock()
        preview_service = MagicMock()
        surface_service = MagicMock()
        visibility_service = MagicMock()
        visibility_service.create_monitor.return_value = None
        session_backend = MagicMock()
        window = _window()
        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        _patch_ui_components(monkeypatch)

        result = factory_mod.build_dock_window(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            preview_service=preview_service,
            surface_service=surface_service,
            visibility_service=visibility_service,
            application_registry=application_registry,
            session_backend=session_backend,
            application_launcher=application_launcher,
            icon_loader=icon_loader,
            target_service=target_service,
            recent_applications=recent_applications,
        )

        assert result.window is window
        assert window.dodge_monitor is None

    def test_build_dock_window_exposes_realized_dock_rect_to_dodge_monitor(
        self, monkeypatch
    ):
        config = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        theme = MagicMock()
        tracker = MagicMock()
        application_registry = MagicMock()
        application_launcher = MagicMock()
        icon_loader = MagicMock()
        target_service = MagicMock()
        recent_applications = MagicMock()
        preview_service = MagicMock()
        surface_service = MagicMock()
        visibility_service = MagicMock()
        session_backend = MagicMock()
        window = _window()
        window.get_realized.return_value = True
        window.get_position.return_value = (10, 20)
        window.geometry.build_frame.return_value.static_dock_rect = SimpleNamespace(
            x=100,
            y=30,
            w=300,
            h=40,
        )
        window.geometry.build_frame.return_value.background_rect = SimpleNamespace(
            x=100,
            y=230,
            w=300,
            h=40,
        )
        captured: dict[str, object] = {}

        def _make_dodge_monitor(**kwargs):
            captured.update(kwargs)
            monitor = MagicMock()
            monitor.start = MagicMock()
            return monitor

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        _patch_ui_components(monkeypatch)
        visibility_service.create_monitor.side_effect = _make_dodge_monitor

        factory_mod.build_dock_window(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            preview_service=preview_service,
            surface_service=surface_service,
            visibility_service=visibility_service,
            application_registry=application_registry,
            session_backend=session_backend,
            application_launcher=application_launcher,
            icon_loader=icon_loader,
            target_service=target_service,
            recent_applications=recent_applications,
        )

        get_dock_rect = cast(Callable[[], object], captured["get_dock_rect"])
        dock_rect = get_dock_rect()
        assert dock_rect == factory_mod.Rect(x=110, y=50, width=300, height=40)

    def test_build_dock_window_returns_none_rect_until_realized(self, monkeypatch):
        config = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        theme = MagicMock()
        tracker = MagicMock()
        application_registry = MagicMock()
        application_launcher = MagicMock()
        icon_loader = MagicMock()
        target_service = MagicMock()
        recent_applications = MagicMock()
        preview_service = MagicMock()
        surface_service = MagicMock()
        visibility_service = MagicMock()
        session_backend = MagicMock()
        window = _window()
        window.get_realized.return_value = False
        captured: dict[str, object] = {}

        def _make_dodge_monitor(**kwargs):
            captured.update(kwargs)
            monitor = MagicMock()
            monitor.start = MagicMock()
            return monitor

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        _patch_ui_components(monkeypatch)
        visibility_service.create_monitor.side_effect = _make_dodge_monitor

        factory_mod.build_dock_window(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            preview_service=preview_service,
            surface_service=surface_service,
            visibility_service=visibility_service,
            application_registry=application_registry,
            session_backend=session_backend,
            application_launcher=application_launcher,
            icon_loader=icon_loader,
            target_service=target_service,
            recent_applications=recent_applications,
        )

        get_dock_rect = cast(Callable[[], object], captured["get_dock_rect"])
        assert get_dock_rect() is None
