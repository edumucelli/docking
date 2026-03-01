"""Tests for application bootstrap wiring in docking.app."""

from __future__ import annotations

import importlib
import os
import signal
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock


def _load_app_module(monkeypatch, *, vendor_exists: bool = False):
    fake_glib = SimpleNamespace(PRIORITY_HIGH=100, unix_signal_add=MagicMock())
    fake_gtk = SimpleNamespace(main=MagicMock(), main_quit=MagicMock())
    fake_repo = SimpleNamespace(GLib=fake_glib, Gtk=fake_gtk)
    fake_gi = SimpleNamespace(require_version=MagicMock(), repository=fake_repo)

    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)

    # Stub UI modules imported by docking.app so we don't depend on full GI
    # bindings during unit tests.
    ui_stubs = {
        "docking.ui.autohide": "AutoHideController",
        "docking.ui.dnd": "DnDHandler",
        "docking.ui.dock_window": "DockWindow",
        "docking.ui.menu": "MenuHandler",
        "docking.ui.preview": "PreviewPopup",
        "docking.ui.renderer": "DockRenderer",
    }
    for module_name, class_name in ui_stubs.items():
        stub_mod = types.ModuleType(module_name)
        setattr(stub_mod, class_name, type(class_name, (), {}))
        monkeypatch.setitem(sys.modules, module_name, stub_mod)

    monkeypatch.setattr(
        os.path,
        "isdir",
        lambda p: vendor_exists and p == "/usr/lib/docking/vendor",
    )

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
        autohide = MagicMock()
        dnd = MagicMock()
        menu = MagicMock()
        preview = MagicMock()

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
        monkeypatch.setattr(app_mod, "DockWindow", MagicMock(return_value=window))
        monkeypatch.setattr(
            app_mod, "AutoHideController", MagicMock(return_value=autohide)
        )
        monkeypatch.setattr(app_mod, "DnDHandler", MagicMock(return_value=dnd))
        monkeypatch.setattr(app_mod, "MenuHandler", MagicMock(return_value=menu))
        monkeypatch.setattr(app_mod, "PreviewPopup", MagicMock(return_value=preview))

        # When
        app_mod.main()

        # Then
        theme_cls.load.assert_called_once_with("default", 48)
        window.set_autohide_controller.assert_called_once_with(autohide)
        window.set_dnd_handler.assert_called_once_with(dnd)
        window.set_menu_handler.assert_called_once_with(menu)
        preview.set_autohide.assert_called_once_with(autohide)
        window.set_preview_popup.assert_called_once_with(preview)
        model.start_applets.assert_called_once()
        model.stop_applets.assert_called_once()
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
