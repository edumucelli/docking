"""Tests for application bootstrap wiring in docking.app."""

from __future__ import annotations

import importlib
import signal
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


def _load_app_module(monkeypatch, *, vendor_exists: bool = False):
    fake_glib = SimpleNamespace(PRIORITY_HIGH=100, unix_signal_add=MagicMock())
    fake_gtk = SimpleNamespace(main=MagicMock(), main_quit=MagicMock())
    fake_repo = SimpleNamespace(GLib=fake_glib, Gtk=fake_gtk)
    fake_gi = SimpleNamespace(require_version=MagicMock(), repository=fake_repo)

    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)

    # Stub platform/UI modules imported by docking.app so we don't depend on the
    # full GI-backed runtime during unit tests.
    platform_pkg = types.ModuleType("docking.platform")
    platform_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "docking.platform", platform_pkg)

    ui_pkg = types.ModuleType("docking.ui")
    ui_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "docking.ui", ui_pkg)

    stub_modules = {
        "docking.platform.environment": {
            "apply_tweaks": lambda **_kwargs: None,
            "detect_desktop": lambda: "test",
        },
        "docking.platform.launcher": {
            "Launcher": type("Launcher", (), {}),
        },
        "docking.platform.model": {
            "DockModel": type("DockModel", (), {}),
        },
        "docking.platform.window_tracker": {
            "WindowTracker": type("WindowTracker", (), {}),
        },
        "docking.ui.factory": {
            "build_dock_window": lambda **_kwargs: None,
        },
        "docking.ui.renderer": {
            "DockRenderer": type("DockRenderer", (), {}),
        },
        "docking.ipc": {
            "DockItemsService": type("DockItemsService", (), {}),
        },
    }
    for module_name, members in stub_modules.items():
        stub_mod = types.ModuleType(module_name)
        for name, value in members.items():
            setattr(stub_mod, name, value)
        monkeypatch.setitem(sys.modules, module_name, stub_mod)

    vendor_dir = "/usr/lib/docking/vendor"
    path_is_dir = Path.is_dir

    def _fake_is_dir(path: Path) -> bool:
        if str(path) == vendor_dir:
            return vendor_exists
        return path_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", _fake_is_dir)

    sys.modules.pop("docking.app", None)
    return importlib.import_module("docking.app"), fake_glib, fake_gtk


class TestAppImport:
    def test_import_inserts_vendor_path_when_present(self, monkeypatch):
        # Given
        vendor_dir = "/usr/lib/docking/vendor"
        while vendor_dir in sys.path:
            sys.path.remove(vendor_dir)
        # When
        _mod, _glib, _gtk = _load_app_module(monkeypatch, vendor_exists=True)
        # Then
        assert sys.path[0] == vendor_dir
        sys.path.remove(vendor_dir)


class TestAppMain:
    def test_main_builds_runtime_graph_and_starts_loop(self, monkeypatch):
        # Given
        app_mod, fake_glib, fake_gtk = _load_app_module(monkeypatch)

        config = SimpleNamespace(theme="default", icon_size=48)
        theme = MagicMock()
        launcher = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        tracker = MagicMock()
        window = MagicMock()
        items_service = MagicMock()

        config_cls = MagicMock()
        config_cls.load.return_value = config
        monkeypatch.setattr(app_mod, "Config", config_cls)

        theme_cls = MagicMock()
        theme_cls.load.return_value = theme
        monkeypatch.setattr(app_mod, "Theme", theme_cls)

        monkeypatch.setattr(app_mod, "Launcher", MagicMock(return_value=launcher))
        monkeypatch.setattr(app_mod, "DockModel", MagicMock(return_value=model))
        monkeypatch.setattr(app_mod, "DockRenderer", MagicMock(return_value=renderer))
        monkeypatch.setattr(app_mod, "WindowTracker", MagicMock(return_value=tracker))
        factory = MagicMock(return_value=window)
        monkeypatch.setattr(app_mod, "build_dock_window", factory)
        monkeypatch.setattr(
            app_mod, "DockItemsService", MagicMock(return_value=items_service)
        )

        # When
        app_mod.main()

        # Then
        theme_cls.load.assert_called_once_with(name="default", icon_size=48)
        factory.assert_called_once_with(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=tracker,
            launcher=launcher,
        )
        model.start_applets.assert_called_once()
        model.stop_applets.assert_called_once()
        items_service.start.assert_called_once()
        items_service.stop.assert_called_once()
        fake_gtk.main.assert_called_once()

        assert fake_glib.unix_signal_add.call_count == 2
        sig_calls = [c.args[1] for c in fake_glib.unix_signal_add.call_args_list]
        assert signal.SIGINT in sig_calls
        assert signal.SIGTERM in sig_calls
        for call in fake_glib.unix_signal_add.call_args_list:
            assert call.args[2] is app_mod._quit

    def test_quit_requests_gtk_main_quit(self, monkeypatch):
        # Given
        app_mod, _fake_glib, fake_gtk = _load_app_module(monkeypatch)
        # When
        result = app_mod._quit()
        # Then
        assert result is False
        fake_gtk.main_quit.assert_called_once()
