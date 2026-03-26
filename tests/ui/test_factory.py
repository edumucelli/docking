"""Tests for UI graph assembly in docking.ui.factory."""

from __future__ import annotations

from unittest.mock import MagicMock

import docking.ui.factory as factory_mod


class TestBuildDockWindow:
    def test_build_dock_window_wires_components_and_attaches_once(self, monkeypatch):
        config = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        theme = MagicMock()
        tracker = MagicMock()
        launcher = MagicMock()

        window = MagicMock()
        window.geometry = MagicMock()
        autohide = MagicMock()
        runtime = MagicMock()
        settings = MagicMock()
        dnd = MagicMock()
        menu = MagicMock()
        preview = MagicMock()
        about = MagicMock()
        dodge_monitor = MagicMock()

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        monkeypatch.setattr(
            factory_mod, "AutoHideController", MagicMock(return_value=autohide)
        )
        monkeypatch.setattr(
            factory_mod,
            "WindowDodgeMonitor",
            MagicMock(return_value=dodge_monitor),
        )
        monkeypatch.setattr(factory_mod, "DockRuntime", MagicMock(return_value=runtime))
        monkeypatch.setattr(
            factory_mod,
            "SettingsWindowController",
            MagicMock(return_value=settings),
        )
        monkeypatch.setattr(factory_mod, "DnDHandler", MagicMock(return_value=dnd))
        monkeypatch.setattr(factory_mod, "MenuHandler", MagicMock(return_value=menu))
        monkeypatch.setattr(
            factory_mod, "PreviewPopup", MagicMock(return_value=preview)
        )
        monkeypatch.setattr(
            factory_mod,
            "AboutDialogController",
            MagicMock(return_value=about),
        )

        result = factory_mod.build_dock_window(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            launcher=launcher,
        )

        assert result is window
        dodge_monitor.start.assert_called_once_with()
        window.attach_components.assert_called_once()
        attached = window.attach_components.call_args.args[0]
        assert attached.autohide is autohide
        assert attached.dnd is dnd
        assert attached.menu is menu
        assert attached.preview is preview

    def test_build_dock_window_exposes_realized_dock_rect_to_dodge_monitor(
        self, monkeypatch
    ):
        config = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        theme = MagicMock()
        tracker = MagicMock()
        launcher = MagicMock()

        window = MagicMock()
        window.geometry = MagicMock()
        window.get_realized.return_value = True
        window.get_position.return_value = (10, 20)
        window.get_size.return_value = (300, 40)
        captured: dict[str, object] = {}

        def _make_dodge_monitor(**kwargs):
            captured.update(kwargs)
            monitor = MagicMock()
            monitor.start = MagicMock()
            return monitor

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        monkeypatch.setattr(factory_mod, "AutoHideController", MagicMock())
        monkeypatch.setattr(factory_mod, "WindowDodgeMonitor", _make_dodge_monitor)
        monkeypatch.setattr(factory_mod, "DockRuntime", MagicMock())
        monkeypatch.setattr(factory_mod, "DockDragRuntime", MagicMock())
        monkeypatch.setattr(factory_mod, "AboutDialogController", MagicMock())
        monkeypatch.setattr(factory_mod, "SettingsWindowController", MagicMock())
        monkeypatch.setattr(factory_mod, "DnDHandler", MagicMock())
        monkeypatch.setattr(factory_mod, "MenuHandler", MagicMock())
        monkeypatch.setattr(factory_mod, "PreviewPopup", MagicMock())

        factory_mod.build_dock_window(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            launcher=launcher,
        )

        dock_rect = captured["get_dock_rect"]()
        assert dock_rect == factory_mod.ScreenRect(x=10, y=20, width=300, height=40)

    def test_build_dock_window_returns_none_rect_until_realized(self, monkeypatch):
        config = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        theme = MagicMock()
        tracker = MagicMock()
        launcher = MagicMock()

        window = MagicMock()
        window.geometry = MagicMock()
        window.get_realized.return_value = False
        captured: dict[str, object] = {}

        def _make_dodge_monitor(**kwargs):
            captured.update(kwargs)
            monitor = MagicMock()
            monitor.start = MagicMock()
            return monitor

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        monkeypatch.setattr(factory_mod, "AutoHideController", MagicMock())
        monkeypatch.setattr(factory_mod, "WindowDodgeMonitor", _make_dodge_monitor)
        monkeypatch.setattr(factory_mod, "DockRuntime", MagicMock())
        monkeypatch.setattr(factory_mod, "DockDragRuntime", MagicMock())
        monkeypatch.setattr(factory_mod, "AboutDialogController", MagicMock())
        monkeypatch.setattr(factory_mod, "SettingsWindowController", MagicMock())
        monkeypatch.setattr(factory_mod, "DnDHandler", MagicMock())
        monkeypatch.setattr(factory_mod, "MenuHandler", MagicMock())
        monkeypatch.setattr(factory_mod, "PreviewPopup", MagicMock())

        factory_mod.build_dock_window(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            launcher=launcher,
        )

        assert captured["get_dock_rect"]() is None
