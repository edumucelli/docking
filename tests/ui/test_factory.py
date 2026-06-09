"""Tests for UI graph assembly in docking.ui.factory."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import docking.ui.factory as factory_mod
from docking.platform.backends.base import DisplayServer, PlatformCapabilities

_FAKE_CAPABILITIES = PlatformCapabilities()
_FAKE_DISPLAY_SERVER = DisplayServer.X11


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
        preview_service = MagicMock()
        surface_service = MagicMock()
        visibility_service = MagicMock()

        window = MagicMock()
        window.autohide = MagicMock()
        dodge_monitor = MagicMock()

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
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
            capabilities=_FAKE_CAPABILITIES,
            display_server=_FAKE_DISPLAY_SERVER,
            launcher=launcher,
        )

        assert result is window
        factory_mod.DockWindow.assert_called_once_with(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            launcher=launcher,
            preview_service=preview_service,
            surface_service=surface_service,
            capabilities=_FAKE_CAPABILITIES,
            display_server=_FAKE_DISPLAY_SERVER,
        )
        kwargs = visibility_service.create_monitor.call_args.kwargs
        assert callable(kwargs["get_dock_rect"])
        assert kwargs["on_change"] is window.autohide.set_window_should_hide
        dodge_monitor.start.assert_called_once_with()
        assert window.dodge_monitor is dodge_monitor

    def test_build_dock_window_allows_unsupported_visibility_service(self, monkeypatch):
        config = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        theme = MagicMock()
        tracker = MagicMock()
        launcher = MagicMock()
        preview_service = MagicMock()
        surface_service = MagicMock()
        visibility_service = MagicMock()
        visibility_service.create_monitor.return_value = None

        window = MagicMock()
        window.autohide = MagicMock()
        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))

        result = factory_mod.build_dock_window(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            preview_service=preview_service,
            surface_service=surface_service,
            visibility_service=visibility_service,
            capabilities=_FAKE_CAPABILITIES,
            display_server=_FAKE_DISPLAY_SERVER,
            launcher=launcher,
        )

        assert result is window
        assert window.dodge_monitor is None

    def test_build_dock_window_exposes_realized_dock_rect_to_dodge_monitor(
        self, monkeypatch
    ):
        config = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        theme = MagicMock()
        tracker = MagicMock()
        launcher = MagicMock()
        preview_service = MagicMock()
        surface_service = MagicMock()
        visibility_service = MagicMock()

        window = MagicMock()
        window.autohide = MagicMock()
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
            capabilities=_FAKE_CAPABILITIES,
            display_server=_FAKE_DISPLAY_SERVER,
            launcher=launcher,
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
        launcher = MagicMock()
        preview_service = MagicMock()
        surface_service = MagicMock()
        visibility_service = MagicMock()

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
            capabilities=_FAKE_CAPABILITIES,
            display_server=_FAKE_DISPLAY_SERVER,
            launcher=launcher,
        )

        get_dock_rect = cast(Callable[[], object], captured["get_dock_rect"])
        assert get_dock_rect() is None
