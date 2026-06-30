"""Tests for UI graph assembly in docking.ui.factory."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import docking.ui.factory as factory_mod


def _inputs() -> dict[str, MagicMock]:
    return {
        "config": MagicMock(),
        "model": MagicMock(),
        "renderer": MagicMock(),
        "theme": MagicMock(),
        "window_tracker": MagicMock(),
        "preview_service": MagicMock(),
        "surface_service": MagicMock(),
        "visibility_service": MagicMock(),
        "launcher": MagicMock(),
        "session_backend": MagicMock(),
    }


def _window() -> MagicMock:
    window = MagicMock()
    window.runtime = MagicMock()
    window.autohide = MagicMock()
    window.geometry = MagicMock()
    return window


class TestBuildDockUi:
    def test_build_dock_ui_wires_window_controllers_and_dodge_monitor(
        self, monkeypatch
    ):
        inputs = _inputs()
        window = _window()
        update_checker = MagicMock()
        about = MagicMock()
        diagnostics = MagicMock()
        settings = MagicMock()
        menu = MagicMock()
        new_year = MagicMock()
        dodge_monitor = MagicMock()
        inputs["visibility_service"].create_monitor.return_value = dodge_monitor

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        monkeypatch.setattr(
            factory_mod,
            "UpdateCheckController",
            MagicMock(return_value=update_checker),
        )
        monkeypatch.setattr(
            factory_mod,
            "AboutDialogController",
            MagicMock(return_value=about),
        )
        monkeypatch.setattr(
            factory_mod,
            "DiagnosticsDialogController",
            MagicMock(return_value=diagnostics),
        )
        monkeypatch.setattr(
            factory_mod,
            "SettingsWindowController",
            MagicMock(return_value=settings),
        )
        monkeypatch.setattr(factory_mod, "MenuHandler", MagicMock(return_value=menu))
        monkeypatch.setattr(
            factory_mod,
            "NewYearGreetingController",
            MagicMock(return_value=new_year),
        )

        result = factory_mod.build_dock_ui(**inputs)

        assert result.window is window
        factory_mod.DockWindow.assert_called_once_with(
            config=inputs["config"],
            model=inputs["model"],
            renderer=inputs["renderer"],
            theme=inputs["theme"],
            window_tracker=inputs["window_tracker"],
            launcher=inputs["launcher"],
            preview_service=inputs["preview_service"],
            surface_service=inputs["surface_service"],
            session_backend=inputs["session_backend"],
        )
        factory_mod.UpdateCheckController.assert_called_once_with(
            config=inputs["config"],
            anchor_provider=window,
        )
        factory_mod.AboutDialogController.assert_called_once_with(parent=window)
        factory_mod.DiagnosticsDialogController.assert_called_once_with(
            parent=window,
            backend=inputs["session_backend"],
        )
        factory_mod.SettingsWindowController.assert_called_once_with(
            parent=window,
            runtime=window.runtime,
            model=inputs["model"],
            config=inputs["config"],
            updates=update_checker,
        )
        factory_mod.MenuHandler.assert_called_once_with(
            about=about,
            settings=settings,
            diagnostics=diagnostics,
            runtime=window.runtime,
            model=inputs["model"],
            config=inputs["config"],
            window_tracker=inputs["window_tracker"],
            preview_service=inputs["preview_service"],
            geometry_builder=window.geometry,
            launcher=inputs["launcher"],
            dock_window=window,
        )
        window.set_menu_handler.assert_called_once_with(menu)
        factory_mod.NewYearGreetingController.assert_called_once_with(
            anchor_provider=window
        )

        kwargs = inputs["visibility_service"].create_monitor.call_args.kwargs
        assert callable(kwargs["get_dock_rect"])
        assert kwargs["on_change"] is window.autohide.set_window_should_hide
        dodge_monitor.start.assert_called_once_with()
        assert window.dodge_monitor is dodge_monitor

        result.start_startup_ui()
        new_year.start.assert_called_once_with()
        update_checker.start.assert_called_once_with()

        result.stop_startup_ui()
        update_checker.stop.assert_called_once_with()
        new_year.stop.assert_called_once_with()

    def test_build_dock_ui_allows_unsupported_visibility_service(self, monkeypatch):
        inputs = _inputs()
        window = _window()
        inputs["visibility_service"].create_monitor.return_value = None

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        monkeypatch.setattr(factory_mod, "UpdateCheckController", MagicMock())
        monkeypatch.setattr(factory_mod, "AboutDialogController", MagicMock())
        monkeypatch.setattr(factory_mod, "DiagnosticsDialogController", MagicMock())
        monkeypatch.setattr(factory_mod, "SettingsWindowController", MagicMock())
        monkeypatch.setattr(factory_mod, "MenuHandler", MagicMock())
        monkeypatch.setattr(factory_mod, "NewYearGreetingController", MagicMock())

        result = factory_mod.build_dock_ui(**inputs)

        assert result.window is window
        assert window.dodge_monitor is None

    def test_build_dock_ui_exposes_realized_dock_rect_to_dodge_monitor(
        self, monkeypatch
    ):
        inputs = _inputs()
        window = _window()
        window.get_realized.return_value = True
        window.get_position.return_value = (10, 20)
        window.geometry.build_frame.return_value.background_rect = SimpleNamespace(
            x=100,
            y=30,
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
        monkeypatch.setattr(factory_mod, "UpdateCheckController", MagicMock())
        monkeypatch.setattr(factory_mod, "AboutDialogController", MagicMock())
        monkeypatch.setattr(factory_mod, "DiagnosticsDialogController", MagicMock())
        monkeypatch.setattr(factory_mod, "SettingsWindowController", MagicMock())
        monkeypatch.setattr(factory_mod, "MenuHandler", MagicMock())
        monkeypatch.setattr(factory_mod, "NewYearGreetingController", MagicMock())
        inputs["visibility_service"].create_monitor.side_effect = _make_dodge_monitor

        factory_mod.build_dock_ui(**inputs)

        get_dock_rect = cast(Callable[[], object], captured["get_dock_rect"])
        dock_rect = get_dock_rect()
        assert dock_rect == factory_mod.Rect(x=110, y=50, width=300, height=40)

    def test_build_dock_ui_returns_none_rect_until_realized(self, monkeypatch):
        inputs = _inputs()
        window = _window()
        window.get_realized.return_value = False
        captured: dict[str, object] = {}

        def _make_dodge_monitor(**kwargs):
            captured.update(kwargs)
            monitor = MagicMock()
            monitor.start = MagicMock()
            return monitor

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        monkeypatch.setattr(factory_mod, "UpdateCheckController", MagicMock())
        monkeypatch.setattr(factory_mod, "AboutDialogController", MagicMock())
        monkeypatch.setattr(factory_mod, "DiagnosticsDialogController", MagicMock())
        monkeypatch.setattr(factory_mod, "SettingsWindowController", MagicMock())
        monkeypatch.setattr(factory_mod, "MenuHandler", MagicMock())
        monkeypatch.setattr(factory_mod, "NewYearGreetingController", MagicMock())
        inputs["visibility_service"].create_monitor.side_effect = _make_dodge_monitor

        factory_mod.build_dock_ui(**inputs)

        get_dock_rect = cast(Callable[[], object], captured["get_dock_rect"])
        assert get_dock_rect() is None
