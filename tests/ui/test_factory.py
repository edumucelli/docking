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
        dnd = MagicMock()
        menu = MagicMock()
        preview = MagicMock()

        monkeypatch.setattr(factory_mod, "DockWindow", MagicMock(return_value=window))
        monkeypatch.setattr(
            factory_mod, "AutoHideController", MagicMock(return_value=autohide)
        )
        monkeypatch.setattr(factory_mod, "DockRuntime", MagicMock(return_value=runtime))
        monkeypatch.setattr(factory_mod, "DnDHandler", MagicMock(return_value=dnd))
        monkeypatch.setattr(factory_mod, "MenuHandler", MagicMock(return_value=menu))
        monkeypatch.setattr(
            factory_mod, "PreviewPopup", MagicMock(return_value=preview)
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
        window.attach_runtime.assert_called_once_with(
            autohide=autohide, dnd=dnd, menu=menu, preview=preview
        )
