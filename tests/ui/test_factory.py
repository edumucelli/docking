"""Tests for UI graph assembly in docking.ui.factory."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock

import docking.ui.factory as factory_mod


class TestBuildDockWindow:
    def test_build_dock_window_constructs_window_and_starts_dodge_monitor(
        self, monkeypatch
    ):
        config = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        theme = MagicMock()
        tracker = MagicMock()
        launcher = MagicMock()

        window = MagicMock()
        window.autohide = MagicMock()
        dodge_monitor = MagicMock()
        preview_service = MagicMock()

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        monkeypatch.setattr(
            factory_mod,
            "X11PreviewService",
            MagicMock(return_value=preview_service),
        )
        monkeypatch.setattr(
            factory_mod,
            "WindowDodgeMonitor",
            MagicMock(return_value=dodge_monitor),
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
        factory_mod.X11PreviewService.assert_called_once_with(window_tracker=tracker)
        factory_mod.DockWindow.assert_called_once_with(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            launcher=launcher,
            preview_service=preview_service,
        )
        dodge_monitor.start.assert_called_once_with()
        assert window.dodge_monitor is dodge_monitor

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
        window.autohide = MagicMock()
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
        monkeypatch.setattr(factory_mod, "X11PreviewService", MagicMock())
        monkeypatch.setattr(factory_mod, "WindowDodgeMonitor", _make_dodge_monitor)

        factory_mod.build_dock_window(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            launcher=launcher,
        )

        get_dock_rect = cast(Callable[[], object], captured["get_dock_rect"])
        dock_rect = get_dock_rect()
        assert dock_rect == factory_mod.ScreenRect(x=10, y=20, width=300, height=40)

    def test_build_dock_window_returns_none_rect_until_realized(self, monkeypatch):
        config = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        theme = MagicMock()
        tracker = MagicMock()
        launcher = MagicMock()

        window = MagicMock()
        window.autohide = MagicMock()
        window.get_realized.return_value = False
        captured: dict[str, object] = {}

        def _make_dodge_monitor(**kwargs):
            captured.update(kwargs)
            monitor = MagicMock()
            monitor.start = MagicMock()
            return monitor

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        monkeypatch.setattr(factory_mod, "X11PreviewService", MagicMock())
        monkeypatch.setattr(factory_mod, "WindowDodgeMonitor", _make_dodge_monitor)

        factory_mod.build_dock_window(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            launcher=launcher,
        )

        get_dock_rect = cast(Callable[[], object], captured["get_dock_rect"])
        assert get_dock_rect() is None
