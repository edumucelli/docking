"""Tests for application bootstrap wiring in docking.app."""

from __future__ import annotations

import importlib
import runpy
import signal
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


def _load_app_module(monkeypatch, *, vendor_exists: bool = False):
    fake_glib = SimpleNamespace(
        PRIORITY_HIGH=100,
        set_prgname=MagicMock(),
        unix_signal_add=MagicMock(),
        idle_add=MagicMock(),
        timeout_add_seconds=MagicMock(return_value=77),
    )
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

    backends_pkg = types.ModuleType("docking.platform.backends")
    backends_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "docking.platform.backends", backends_pkg)

    x11_pkg = types.ModuleType("docking.platform.backends.x11")
    x11_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "docking.platform.backends.x11", x11_pkg)

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
        "docking.platform.backends.selection": {
            "create_session_backend": lambda **_kwargs: None,
        },
        "docking.platform.unity": {
            "UnityLauncherListener": type("UnityLauncherListener", (), {}),
        },
        "docking.ui.factory": {
            "build_dock_ui": lambda **_kwargs: None,
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

        config = SimpleNamespace(theme="default", icon_size=48, transparency=1.0)
        theme = MagicMock()
        applied_theme = object()
        theme.with_opacity.return_value = applied_theme
        launcher = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        tracker = MagicMock()
        preview_service = MagicMock()
        backend = MagicMock()
        backend.windows = tracker
        surface_service = MagicMock()
        visibility_service = MagicMock()
        backend.previews = preview_service
        backend.surface = surface_service
        backend.visibility = visibility_service
        backend.desktop_actions = None
        backend.workspaces = None
        backend.window_picker = None
        backend.idle = None
        backend.screen_capture = None
        unity = MagicMock()
        window = MagicMock()
        ui = SimpleNamespace(
            window=window,
            start_startup_ui=MagicMock(),
            stop_startup_ui=MagicMock(),
        )
        items_service = MagicMock()
        call_order: list[str] = []

        window.show_all.side_effect = lambda: call_order.append("show_all")
        ui.start_startup_ui.side_effect = lambda: call_order.append("startup_ui_start")
        unity.start.side_effect = lambda: call_order.append("unity_start")
        items_service.start.side_effect = lambda: call_order.append("items_start")
        model.start_applets.side_effect = lambda: call_order.append("applets_start")

        def idle_add(callback, *args):
            call_order.append("idle_add")
            return callback(*args)

        fake_glib.idle_add.side_effect = idle_add

        config_cls = MagicMock()
        config_cls.load.return_value = config
        monkeypatch.setattr(app_mod, "Config", config_cls)

        theme_cls = MagicMock()
        theme_cls.load.return_value = theme
        monkeypatch.setattr(app_mod, "Theme", theme_cls)

        monkeypatch.setattr(app_mod, "Launcher", MagicMock(return_value=launcher))
        monkeypatch.setattr(app_mod, "DockModel", MagicMock(return_value=model))
        monkeypatch.setattr(app_mod, "DockRenderer", MagicMock(return_value=renderer))
        monkeypatch.setattr(
            app_mod,
            "create_session_backend",
            MagicMock(return_value=backend),
        )
        monkeypatch.setattr(
            app_mod,
            "UnityLauncherListener",
            MagicMock(return_value=unity),
        )
        factory = MagicMock(return_value=ui)
        monkeypatch.setattr(app_mod, "build_dock_ui", factory)
        monkeypatch.setattr(
            app_mod, "DockItemsService", MagicMock(return_value=items_service)
        )

        # When
        app_mod.main()

        # Then
        theme_cls.load.assert_called_once_with(name="default", icon_size=48)
        theme.with_opacity.assert_called_once_with(1.0)
        services = model.set_applet_services.call_args.args[0]
        assert services.desktop_actions is None
        assert services.workspaces is None
        assert services.window_picker is None
        assert services.idle is None
        assert services.screen_capture is None
        factory.assert_called_once_with(
            config=config,
            model=model,
            renderer=renderer,
            theme=applied_theme,
            window_tracker=tracker,
            preview_service=preview_service,
            surface_service=surface_service,
            visibility_service=visibility_service,
            session_backend=backend,
            launcher=launcher,
        )
        backend.start.assert_called_once()
        backend.stop.assert_called_once()
        model.start_applets.assert_called_once()
        model.stop_applets.assert_called_once()
        items_service.start.assert_called_once()
        items_service.stop.assert_called_once()
        unity.start.assert_called_once()
        unity.stop.assert_called_once()
        ui.start_startup_ui.assert_called_once()
        ui.stop_startup_ui.assert_called_once()
        fake_glib.idle_add.assert_called_once_with(
            app_mod._start_runtime,
            items_service,
            model,
            backend,
        )
        fake_gtk.main.assert_called_once()
        assert call_order == [
            "unity_start",
            "show_all",
            "startup_ui_start",
            "idle_add",
            "items_start",
            "applets_start",
        ]

        assert fake_glib.unix_signal_add.call_count == 2
        sig_calls = [c.args[1] for c in fake_glib.unix_signal_add.call_args_list]
        assert signal.SIGINT in sig_calls
        assert signal.SIGTERM in sig_calls
        for call in fake_glib.unix_signal_add.call_args_list:
            assert call.args[2] is app_mod._quit

    def test_module_entrypoint_invokes_main(self, monkeypatch):
        # Given
        _app_mod, fake_glib, fake_gtk = _load_app_module(monkeypatch)

        config = SimpleNamespace(theme="default", icon_size=48, transparency=1.0)
        theme = MagicMock()
        applied_theme = object()
        theme.with_opacity.return_value = applied_theme
        launcher = MagicMock()
        model = MagicMock()
        renderer = MagicMock()
        tracker = MagicMock()
        preview_service = MagicMock()
        backend = MagicMock()
        backend.windows = tracker
        surface_service = MagicMock()
        visibility_service = MagicMock()
        backend.previews = preview_service
        backend.surface = surface_service
        backend.visibility = visibility_service
        unity = MagicMock()
        window = MagicMock()
        ui = SimpleNamespace(
            window=window,
            start_startup_ui=MagicMock(),
            stop_startup_ui=MagicMock(),
        )
        items_service = MagicMock()
        call_order: list[str] = []

        window.show_all.side_effect = lambda: call_order.append("show_all")
        ui.start_startup_ui.side_effect = lambda: call_order.append("startup_ui_start")
        unity.start.side_effect = lambda: call_order.append("unity_start")
        items_service.start.side_effect = lambda: call_order.append("items_start")
        model.start_applets.side_effect = lambda: call_order.append("applets_start")

        def idle_add(callback, *args):
            call_order.append("idle_add")
            return callback(*args)

        fake_glib.idle_add.side_effect = idle_add

        config_cls = MagicMock()
        config_cls.load.return_value = config
        theme_cls = MagicMock()
        theme_cls.load.return_value = theme
        launcher_cls = MagicMock(return_value=launcher)
        model_cls = MagicMock(return_value=model)
        renderer_cls = MagicMock(return_value=renderer)
        backend_cls = MagicMock(return_value=backend)
        unity_cls = MagicMock(return_value=unity)
        factory = MagicMock(return_value=ui)
        items_service_cls = MagicMock(return_value=items_service)

        monkeypatch.setattr(sys.modules["docking.core.config"], "Config", config_cls)
        monkeypatch.setattr(sys.modules["docking.core.theme"], "Theme", theme_cls)
        monkeypatch.setattr(
            sys.modules["docking.platform.launcher"], "Launcher", launcher_cls
        )
        monkeypatch.setattr(
            sys.modules["docking.platform.model"], "DockModel", model_cls
        )
        monkeypatch.setattr(
            sys.modules["docking.ui.renderer"], "DockRenderer", renderer_cls
        )
        monkeypatch.setattr(
            sys.modules["docking.platform.backends.selection"],
            "create_session_backend",
            backend_cls,
        )
        monkeypatch.setattr(
            sys.modules["docking.platform.unity"], "UnityLauncherListener", unity_cls
        )
        monkeypatch.setattr(sys.modules["docking.ui.factory"], "build_dock_ui", factory)
        monkeypatch.setattr(
            sys.modules["docking.ipc"], "DockItemsService", items_service_cls
        )

        sys.modules.pop("__main__", None)
        sys.modules.pop("docking.app", None)

        # When
        runpy.run_module("docking.app", run_name="__main__")

        # Then
        theme_cls.load.assert_called_once_with(name="default", icon_size=48)
        theme.with_opacity.assert_called_once_with(1.0)
        factory.assert_called_once()
        items_service_cls.assert_called_once_with(model=model, window=window)
        fake_gtk.main.assert_called_once()
        backend.start.assert_called_once()
        backend.stop.assert_called_once()
        unity.start.assert_called_once()
        unity.stop.assert_called_once()
        ui.start_startup_ui.assert_called_once()
        ui.stop_startup_ui.assert_called_once()
        assert call_order == [
            "unity_start",
            "show_all",
            "startup_ui_start",
            "idle_add",
            "items_start",
            "applets_start",
        ]

    def test_quit_requests_gtk_main_quit(self, monkeypatch):
        # Given
        app_mod, fake_glib, fake_gtk = _load_app_module(monkeypatch)
        # When
        result = app_mod._quit()
        # Then
        assert result is False
        fake_gtk.main_quit.assert_called_once()
        fake_glib.timeout_add_seconds.assert_called_once_with(3, app_mod._force_quit)

    def test_quit_schedules_force_quit_once(self, monkeypatch):
        # Given
        app_mod, fake_glib, fake_gtk = _load_app_module(monkeypatch)
        # When
        app_mod._quit()
        app_mod._quit()
        # Then
        assert fake_gtk.main_quit.call_count == 2
        fake_glib.timeout_add_seconds.assert_called_once_with(3, app_mod._force_quit)

    def test_force_quit_exits_process(self, monkeypatch):
        # Given
        app_mod, _fake_glib, _fake_gtk = _load_app_module(monkeypatch)
        exit_mock = MagicMock()
        monkeypatch.setattr(app_mod.os, "_exit", exit_mock)
        # When
        app_mod._force_quit()
        # Then
        exit_mock.assert_called_once_with(0)
