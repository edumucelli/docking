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

import pytest


def _runtime_config() -> SimpleNamespace:
    return SimpleNamespace(
        theme="default",
        icon_size=48,
        transparency=1.0,
        show_recent_apps=False,
        recent_apps_max=7,
        recent_apps_retention_days=30,
        recent_apps=[],
    )


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

    applications_pkg = types.ModuleType("docking.platform.applications")
    applications_pkg.__path__ = []
    monkeypatch.setitem(
        sys.modules,
        "docking.platform.applications",
        applications_pkg,
    )

    backends_pkg = types.ModuleType("docking.platform.backends")
    backends_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "docking.platform.backends", backends_pkg)

    x11_pkg = types.ModuleType("docking.platform.backends.x11")
    x11_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "docking.platform.backends.x11", x11_pkg)

    ui_pkg = types.ModuleType("docking.ui")
    ui_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "docking.ui", ui_pkg)

    class _LaunchProvenanceStore:
        pass

    class _ProcessIdentityService:
        def __init__(self, provenance_store):
            self.provenance_store = provenance_store

    class _ApplicationLauncher:
        def __init__(self, registry, provenance_store):
            self.registry = registry
            self.provenance_store = provenance_store

    class _IconLoader:
        pass

    class _TargetService:
        def __init__(self, *, icon_loader):
            self.icon_loader = icon_loader

    class _RecentApplicationsPersistence:
        def __init__(self, config):
            self.config = config

    class _RecentApplications:
        def __init__(self, registry, persistence):
            self.registry = registry
            self.persistence = persistence

    configured_application_launcher = None

    def _configure_application_launcher(application_launcher):
        nonlocal configured_application_launcher
        previous = configured_application_launcher
        configured_application_launcher = application_launcher
        return previous

    def _reset_application_launcher(previous=None):
        nonlocal configured_application_launcher
        configured_application_launcher = previous

    def _get_configured_application_launcher():
        return configured_application_launcher

    default_process_identity_service = object()
    configured_process_identity_service = default_process_identity_service

    def _configure_process_identity_service(service):
        nonlocal configured_process_identity_service
        previous = configured_process_identity_service
        configured_process_identity_service = service
        return previous

    def _reset_process_identity_service(previous=None):
        nonlocal configured_process_identity_service
        configured_process_identity_service = (
            default_process_identity_service if previous is None else previous
        )

    def _get_process_identity_service():
        return configured_process_identity_service

    stub_modules = {
        "docking.platform.gamescope": {
            "prepare_gamescope_wayland_environment": lambda: False,
        },
        "docking.platform.launcher": {
            "configure_application_launcher": _configure_application_launcher,
            "get_configured_application_launcher": (
                _get_configured_application_launcher
            ),
            "reset_application_launcher": _reset_application_launcher,
        },
        "docking.platform.process_identity": {
            "configure_process_identity_service": _configure_process_identity_service,
            "get_process_identity_service": _get_process_identity_service,
            "reset_process_identity_service": _reset_process_identity_service,
        },
        "docking.platform.environment": {
            "apply_tweaks": lambda **_kwargs: None,
            "detect_desktop": lambda: "test",
        },
        "docking.platform.applications.registry": {
            "ApplicationRegistry": type("ApplicationRegistry", (), {}),
        },
        "docking.platform.applications.identity": {
            "LaunchProvenanceStore": _LaunchProvenanceStore,
            "ProcessIdentityService": _ProcessIdentityService,
        },
        "docking.platform.applications.launcher": {
            "ApplicationLauncher": _ApplicationLauncher,
        },
        "docking.platform.applications.recents": {
            "RecentApplications": _RecentApplications,
            "RecentApplicationsPersistence": _RecentApplicationsPersistence,
        },
        "docking.platform.icons": {
            "IconLoader": _IconLoader,
        },
        "docking.platform.targets": {
            "TargetService": _TargetService,
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
        "docking.platform.status_notifier": {
            "StatusNotifierNotificationBridge": type(
                "StatusNotifierNotificationBridge",
                (),
                {},
            ),
        },
        "docking.ui.factory": {
            "build_dock_window": lambda **_kwargs: None,
        },
        "docking.ui.new_year": {
            "NewYearGreetingController": type("NewYearGreetingController", (), {}),
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
    platform_pkg.launcher = sys.modules["docking.platform.launcher"]
    platform_pkg.process_identity = sys.modules["docking.platform.process_identity"]

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
    def test_registry_first_run_factory_uses_content_type_defaults(self, monkeypatch):
        app_mod, _fake_glib, _fake_gtk = _load_app_module(monkeypatch)
        registry = MagicMock()
        defaults = {
            "x-scheme-handler/http": "browser-default.desktop",
            "inode/directory": "files-default.desktop",
            "text/plain": "editor-default.desktop",
            "x-scheme-handler/mailto": "mail-default.desktop",
        }
        available = set(defaults.values())
        registry.get.side_effect = lambda desktop_id: (
            object() if desktop_id in available else None
        )
        registry.default_for_content_type.side_effect = lambda content_type: (
            SimpleNamespace(desktop_id=defaults[content_type])
            if content_type in defaults
            else None
        )

        pinned = app_mod._initial_pinned_for_registry(registry)

        assert [entry.target for entry in pinned if entry.kind == "app"] == [
            "browser-default.desktop",
            "files-default.desktop",
            "editor-default.desktop",
            "mail-default.desktop",
        ]

    @pytest.mark.parametrize(
        "failed_stage",
        (None, "backend", "items_service", "applets"),
        ids=("success", "backend-failure", "items-service-failure", "applets-failure"),
    )
    @pytest.mark.parametrize(
        "throwing_cleanup",
        (False, True),
        ids=("clean-stop", "multiple-stop-failures"),
    )
    def test_main_builds_runtime_graph_and_starts_loop(
        self,
        monkeypatch,
        failed_stage,
        throwing_cleanup,
    ):
        # Given
        app_mod, fake_glib, fake_gtk = _load_app_module(monkeypatch)

        config = _runtime_config()
        theme = MagicMock()
        applied_theme = object()
        theme.with_opacity.return_value = applied_theme
        registry = MagicMock()
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
        status_notifications = MagicMock()
        window = MagicMock()
        ui = SimpleNamespace(
            window=window,
            search=MagicMock(),
            start=MagicMock(),
            stop=MagicMock(),
        )
        items_service = MagicMock()
        call_order: list[str] = []
        registry_active = False
        registry.generation = 0

        previous_application_launcher = object()
        previous_process_identity_service = object()
        app_mod.launcher_facade.configure_application_launcher(
            previous_application_launcher
        )
        app_mod.process_identity_facade.configure_process_identity_service(
            previous_process_identity_service
        )

        def refresh_registry():
            registry.generation = 1
            call_order.append("registry_refresh")
            return True

        def start_registry():
            nonlocal registry_active
            registry_active = True
            call_order.append("registry_start")

        def stop_registry():
            nonlocal registry_active
            registry_active = False
            call_order.append("registry_stop")

        registry.refresh.side_effect = refresh_registry
        registry.start.side_effect = start_registry
        registry.stop.side_effect = stop_registry
        registry.get.side_effect = lambda _desktop_id: (
            call_order.append("first_run_resolve") or None
        )
        registry.default_for_content_type.side_effect = lambda _content_type: (
            call_order.append("first_run_default") or None
        )

        window.show_all.side_effect = lambda: call_order.append("show_all")
        ui.start.side_effect = lambda: call_order.append("ui_start")
        unity.start.side_effect = lambda: call_order.append("unity_start")
        status_notifications.start.side_effect = lambda: call_order.append(
            "status_notifications_start"
        )

        throwing_stops = {
            "items_stop",
            "ui_stop",
            "backend_stop",
            "applets_stop",
            "application_facade_reset",
        }

        def cleanup_event(name):
            call_order.append(name)
            if throwing_cleanup and name in throwing_stops:
                raise RuntimeError(f"{name} failed")

        ui.stop.side_effect = lambda: cleanup_event("ui_stop")
        unity.stop.side_effect = lambda: cleanup_event("unity_stop")
        status_notifications.stop.side_effect = lambda: cleanup_event(
            "status_notifications_stop"
        )
        items_service.stop.side_effect = lambda: cleanup_event("items_stop")

        reset_application_launcher = app_mod.launcher_facade.reset_application_launcher
        reset_process_identity_service = (
            app_mod.process_identity_facade.reset_process_identity_service
        )

        def restore_application_launcher(previous=None):
            reset_application_launcher(previous)
            cleanup_event("application_facade_reset")

        def restore_process_identity_service(previous=None):
            reset_process_identity_service(previous)
            cleanup_event("process_identity_facade_reset")

        monkeypatch.setattr(
            app_mod.launcher_facade,
            "reset_application_launcher",
            restore_application_launcher,
        )
        monkeypatch.setattr(
            app_mod.process_identity_facade,
            "reset_process_identity_service",
            restore_process_identity_service,
        )

        def runtime_start(name):
            assert registry_active
            call_order.append(name)

        backend.start.side_effect = lambda: runtime_start("backend_start")
        items_service.start.side_effect = lambda: runtime_start("items_start")
        model.start_applets.side_effect = lambda: runtime_start("applets_start")
        model.stop_applets.side_effect = lambda: cleanup_event("applets_stop")
        backend.stop.side_effect = lambda: cleanup_event("backend_stop")
        model.close.side_effect = lambda: cleanup_event("model_close")
        if failed_stage is not None:
            {
                "backend": backend.start,
                "items_service": items_service.start,
                "applets": model.start_applets,
            }[failed_stage].side_effect = RuntimeError(f"{failed_stage} failed")

        def idle_add(callback, *args):
            call_order.append("idle_add")
            return callback(*args)

        fake_glib.idle_add.side_effect = idle_add

        config_cls = MagicMock()

        def load_config(**kwargs):
            call_order.append("config_load")
            kwargs["initial_pinned_factory"]()
            return config

        config_cls.load.side_effect = load_config
        monkeypatch.setattr(app_mod, "Config", config_cls)

        theme_cls = MagicMock()
        theme_cls.load.return_value = theme
        monkeypatch.setattr(app_mod, "Theme", theme_cls)

        registry_cls = MagicMock(return_value=registry)
        monkeypatch.setattr(app_mod, "ApplicationRegistry", registry_cls)

        def build_model(**_kwargs):
            call_order.append("model_construct")
            return model

        model_cls = MagicMock(side_effect=build_model)
        monkeypatch.setattr(app_mod, "DockModel", model_cls)
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
        monkeypatch.setattr(
            app_mod,
            "StatusNotifierNotificationBridge",
            MagicMock(return_value=status_notifications),
        )

        def build_ui(**kwargs):
            assert (
                app_mod.launcher_facade.get_configured_application_launcher()
                is kwargs["application_launcher"]
            )
            assert (
                app_mod.process_identity_facade.get_process_identity_service()
                is app_mod.create_session_backend.call_args.kwargs[
                    "process_identity_service"
                ]
            )
            return ui

        factory = MagicMock(side_effect=build_ui)
        monkeypatch.setattr(app_mod, "build_dock_window", factory)
        monkeypatch.setattr(
            app_mod, "DockItemsService", MagicMock(return_value=items_service)
        )

        # When
        app_mod.main()

        # Then
        theme_cls.load.assert_called_once_with(name="default", icon_size=48)
        theme.with_opacity.assert_called_once_with(1.0)
        registry_cls.assert_called_once_with()
        config_cls.load.assert_called_once()
        assert callable(config_cls.load.call_args.kwargs["initial_pinned_factory"])
        initial_services = model_cls.call_args.kwargs["applet_services"]
        application_launcher = initial_services.application_launcher
        icon_loader = model_cls.call_args.kwargs["icon_loader"]
        target_service = model_cls.call_args.kwargs["target_service"]
        assert target_service.icon_loader is icon_loader
        assert initial_services.application_registry is registry
        assert application_launcher.registry is registry
        assert application_launcher.provenance_store is not None
        process_identity_service = app_mod.create_session_backend.call_args.kwargs[
            "process_identity_service"
        ]
        assert (
            process_identity_service.provenance_store
            is application_launcher.provenance_store
        )
        assert model_cls.call_args.kwargs["application_registry"] is registry
        assert model_cls.call_args.kwargs["icon_loader"] is icon_loader
        assert model_cls.call_args.kwargs["target_service"] is target_service
        recent_applications = model_cls.call_args.kwargs["recent_applications"]
        assert recent_applications.registry is registry
        assert recent_applications.persistence.config is config
        backend_call = app_mod.create_session_backend.call_args.kwargs
        assert backend_call["application_registry"] is registry
        assert backend_call["process_identity_service"] is process_identity_service
        assert "launcher" not in backend_call
        assert len(model.set_applet_services.call_args_list) == 2
        assert all(
            call.args[0].application_registry is registry
            for call in model.set_applet_services.call_args_list
        )
        services = model.set_applet_services.call_args.args[0]
        assert services.desktop_actions is None
        assert services.workspaces is None
        assert services.window_picker is None
        assert services.idle is None
        assert services.screen_capture is None
        assert services.search is ui.search
        assert services.application_registry is registry
        assert services.application_launcher is application_launcher
        app_mod.UnityLauncherListener.assert_called_once_with(
            model=model,
            application_registry=registry,
        )
        app_mod.StatusNotifierNotificationBridge.assert_called_once_with(
            model=model,
            application_registry=registry,
        )
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
            application_registry=registry,
            application_launcher=application_launcher,
            icon_loader=icon_loader,
            target_service=target_service,
            recent_applications=recent_applications,
        )
        backend.start.assert_called_once()
        backend.stop.assert_called_once()
        model.start_applets.assert_called_once()
        model.stop_applets.assert_called_once()
        model.close.assert_called_once()
        items_service.start.assert_called_once()
        items_service.stop.assert_called_once()
        unity.start.assert_called_once()
        unity.stop.assert_called_once()
        status_notifications.start.assert_called_once()
        status_notifications.stop.assert_called_once()
        ui.start.assert_called_once()
        ui.stop.assert_called_once()
        registry.refresh.assert_called_once_with()
        registry.start.assert_called_once_with()
        registry.stop.assert_called_once_with()
        fake_glib.idle_add.assert_called_once()
        assert fake_glib.idle_add.call_args.args == (
            app_mod._start_runtime,
            items_service,
            model,
            backend,
        )
        fake_gtk.main.assert_called_once()
        if failed_stage is None:
            lifecycle_order = [
                name for name in call_order if not name.startswith("first_run_")
            ]
            assert lifecycle_order == [
                "registry_refresh",
                "config_load",
                "model_construct",
                "registry_start",
                "status_notifications_start",
                "unity_start",
                "show_all",
                "ui_start",
                "idle_add",
                "backend_start",
                "items_start",
                "applets_start",
                "items_stop",
                "ui_stop",
                "status_notifications_stop",
                "unity_stop",
                "backend_stop",
                "applets_stop",
                "model_close",
                "application_facade_reset",
                "process_identity_facade_reset",
                "registry_stop",
            ]
            assert "first_run_resolve" in call_order
            assert "first_run_default" in call_order
        assert call_order.index("registry_refresh") < call_order.index("config_load")
        assert call_order.index("config_load") < call_order.index("model_construct")
        assert call_order.index("registry_start") < call_order.index("idle_add")
        assert call_order.index("ui_stop") < call_order.index("registry_stop")
        assert call_order.index("status_notifications_stop") < call_order.index(
            "registry_stop"
        )
        assert call_order.index("unity_stop") < call_order.index("registry_stop")
        assert call_order.index("applets_stop") < call_order.index("registry_stop")
        assert call_order.index("backend_stop") < call_order.index("registry_stop")
        assert call_order.index("backend_stop") < call_order.index("model_close")
        assert call_order.index("model_close") < call_order.index("registry_stop")
        assert call_order.index("items_stop") < call_order.index("ui_stop")
        assert call_order.index("ui_stop") < call_order.index(
            "status_notifications_stop"
        )
        assert call_order.index("status_notifications_stop") < call_order.index(
            "unity_stop"
        )
        assert call_order.index("unity_stop") < call_order.index("backend_stop")
        assert call_order.index("backend_stop") < call_order.index("applets_stop")
        assert call_order.index("applets_stop") < call_order.index("model_close")
        assert call_order.index("model_close") < call_order.index(
            "application_facade_reset"
        )
        assert call_order.index("application_facade_reset") < call_order.index(
            "process_identity_facade_reset"
        )
        assert call_order.index("process_identity_facade_reset") < call_order.index(
            "registry_stop"
        )
        assert (
            app_mod.launcher_facade.get_configured_application_launcher()
            is previous_application_launcher
        )
        assert (
            app_mod.process_identity_facade.get_process_identity_service()
            is previous_process_identity_service
        )

        assert fake_glib.unix_signal_add.call_count == 2
        sig_calls = [c.args[1] for c in fake_glib.unix_signal_add.call_args_list]
        assert signal.SIGINT in sig_calls
        assert signal.SIGTERM in sig_calls
        for call in fake_glib.unix_signal_add.call_args_list:
            assert call.args[2] is app_mod._quit

    def test_initial_discovery_failure_never_loads_or_saves_missing_config(
        self,
        monkeypatch,
        tmp_path,
    ):
        app_mod, _fake_glib, _fake_gtk = _load_app_module(monkeypatch)
        missing_config = tmp_path / "missing" / "dock.json"
        initial_pinned_factory = MagicMock()
        monkeypatch.setattr(
            app_mod,
            "_initial_pinned_for_registry",
            initial_pinned_factory,
        )
        config_type = app_mod.Config
        load_config = MagicMock()
        save_config = MagicMock()

        class MissingConfig(config_type):
            @classmethod
            def load(cls, _path=None, *, initial_pinned_factory=None):
                load_config()
                return super().load(
                    missing_config,
                    initial_pinned_factory=initial_pinned_factory,
                )

            def save(self, path=None):
                save_config()
                return super().save(path)

        monkeypatch.setattr(app_mod, "Config", MissingConfig)

        registry = MagicMock()
        registry.generation = 0
        registry.refresh.return_value = False
        monkeypatch.setattr(
            app_mod,
            "ApplicationRegistry",
            MagicMock(return_value=registry),
        )

        with pytest.raises(RuntimeError, match="Initial application discovery failed"):
            app_mod.main()

        load_config.assert_not_called()
        save_config.assert_not_called()
        initial_pinned_factory.assert_not_called()
        assert not missing_config.exists()
        registry.start.assert_not_called()
        registry.stop.assert_called_once_with()

    def test_successful_empty_initial_discovery_reaches_config_load(self, monkeypatch):
        app_mod, _fake_glib, _fake_gtk = _load_app_module(monkeypatch)
        registry = MagicMock()
        registry.generation = 0
        registry.snapshot.return_value = ()

        def refresh_registry():
            registry.generation = 1
            return True

        registry.refresh.side_effect = refresh_registry
        monkeypatch.setattr(
            app_mod,
            "ApplicationRegistry",
            MagicMock(return_value=registry),
        )
        config_cls = MagicMock()
        config_cls.load.side_effect = RuntimeError("config reached")
        monkeypatch.setattr(app_mod, "Config", config_cls)

        with pytest.raises(RuntimeError, match="config reached"):
            app_mod.main()

        config_cls.load.assert_called_once()
        assert callable(config_cls.load.call_args.kwargs["initial_pinned_factory"])
        registry.stop.assert_called_once_with()

    def test_main_stops_owned_registry_when_config_composition_fails(
        self,
        monkeypatch,
    ):
        app_mod, _fake_glib, _fake_gtk = _load_app_module(monkeypatch)
        registry = MagicMock()
        registry.generation = 1
        monkeypatch.setattr(
            app_mod,
            "ApplicationRegistry",
            MagicMock(return_value=registry),
        )
        config_cls = MagicMock()
        config_cls.load.side_effect = RuntimeError("config failed")
        monkeypatch.setattr(app_mod, "Config", config_cls)

        with pytest.raises(RuntimeError, match="config failed"):
            app_mod.main()

        registry.refresh.assert_called_once_with()
        registry.start.assert_not_called()
        registry.stop.assert_called_once_with()

    def test_main_cleans_partial_graph_when_backend_construction_fails(
        self,
        monkeypatch,
    ):
        app_mod, _fake_glib, _fake_gtk = _load_app_module(monkeypatch)
        registry = MagicMock()
        registry.generation = 1
        model = MagicMock()
        cleanup_order: list[str] = []
        model.stop_applets.side_effect = lambda: cleanup_order.append("applets")
        model.close.side_effect = lambda: cleanup_order.append("model listener")
        registry.stop.side_effect = lambda: cleanup_order.append("registry")

        previous_application_launcher = object()
        previous_process_identity_service = object()
        app_mod.launcher_facade.configure_application_launcher(
            previous_application_launcher
        )
        app_mod.process_identity_facade.configure_process_identity_service(
            previous_process_identity_service
        )
        reset_application_launcher = app_mod.launcher_facade.reset_application_launcher
        reset_process_identity_service = (
            app_mod.process_identity_facade.reset_process_identity_service
        )

        def restore_application_launcher(previous=None):
            reset_application_launcher(previous)
            cleanup_order.append("application facade")

        def restore_process_identity_service(previous=None):
            reset_process_identity_service(previous)
            cleanup_order.append("process facade")

        monkeypatch.setattr(
            app_mod.launcher_facade,
            "reset_application_launcher",
            restore_application_launcher,
        )
        monkeypatch.setattr(
            app_mod.process_identity_facade,
            "reset_process_identity_service",
            restore_process_identity_service,
        )
        monkeypatch.setattr(
            app_mod,
            "ApplicationRegistry",
            MagicMock(return_value=registry),
        )
        config_cls = MagicMock()
        config_cls.load.return_value = _runtime_config()
        monkeypatch.setattr(app_mod, "Config", config_cls)
        theme = MagicMock()
        theme.with_opacity.return_value = object()
        theme_cls = MagicMock()
        theme_cls.load.return_value = theme
        monkeypatch.setattr(app_mod, "Theme", theme_cls)
        monkeypatch.setattr(app_mod, "DockModel", MagicMock(return_value=model))
        monkeypatch.setattr(
            app_mod,
            "create_session_backend",
            MagicMock(side_effect=RuntimeError("backend construction failed")),
        )

        with pytest.raises(RuntimeError, match="backend construction failed"):
            app_mod.main()

        assert cleanup_order == [
            "applets",
            "model listener",
            "application facade",
            "process facade",
            "registry",
        ]
        registry.start.assert_not_called()
        assert (
            app_mod.launcher_facade.get_configured_application_launcher()
            is previous_application_launcher
        )
        assert (
            app_mod.process_identity_facade.get_process_identity_service()
            is previous_process_identity_service
        )

    def test_module_entrypoint_invokes_main(self, monkeypatch):
        # Given
        _app_mod, fake_glib, fake_gtk = _load_app_module(monkeypatch)

        config = _runtime_config()
        theme = MagicMock()
        applied_theme = object()
        theme.with_opacity.return_value = applied_theme
        registry = MagicMock()
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
        status_notifications = MagicMock()
        window = MagicMock()
        ui = SimpleNamespace(
            window=window,
            search=MagicMock(),
            start=MagicMock(),
            stop=MagicMock(),
        )
        items_service = MagicMock()
        call_order: list[str] = []
        registry.generation = 0

        def refresh_registry():
            registry.generation = 1
            call_order.append("registry_refresh")

        registry.refresh.side_effect = refresh_registry
        registry.start.side_effect = lambda: call_order.append("registry_start")
        registry.stop.side_effect = lambda: call_order.append("registry_stop")
        window.show_all.side_effect = lambda: call_order.append("show_all")
        ui.start.side_effect = lambda: call_order.append("ui_start")
        unity.start.side_effect = lambda: call_order.append("unity_start")
        status_notifications.start.side_effect = lambda: call_order.append(
            "status_notifications_start"
        )
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
        registry_cls = MagicMock(return_value=registry)
        model_cls = MagicMock(return_value=model)
        renderer_cls = MagicMock(return_value=renderer)
        backend_cls = MagicMock(return_value=backend)
        unity_cls = MagicMock(return_value=unity)
        status_notifications_cls = MagicMock(return_value=status_notifications)
        factory = MagicMock(return_value=ui)
        items_service_cls = MagicMock(return_value=items_service)

        monkeypatch.setattr(sys.modules["docking.core.config"], "Config", config_cls)
        monkeypatch.setattr(sys.modules["docking.core.theme"], "Theme", theme_cls)
        monkeypatch.setattr(
            sys.modules["docking.platform.applications.registry"],
            "ApplicationRegistry",
            registry_cls,
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
        monkeypatch.setattr(
            sys.modules["docking.platform.status_notifier"],
            "StatusNotifierNotificationBridge",
            status_notifications_cls,
        )
        monkeypatch.setattr(
            sys.modules["docking.ui.factory"], "build_dock_window", factory
        )
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
        registry_cls.assert_called_once_with()
        application_launcher = factory.call_args.kwargs["application_launcher"]
        icon_loader = factory.call_args.kwargs["icon_loader"]
        target_service = factory.call_args.kwargs["target_service"]
        assert target_service.icon_loader is icon_loader
        factory.assert_called_once()
        assert factory.call_args.kwargs["application_registry"] is registry
        assert factory.call_args.kwargs["application_launcher"] is application_launcher
        assert factory.call_args.kwargs["icon_loader"] is icon_loader
        assert factory.call_args.kwargs["target_service"] is target_service
        assert (
            factory.call_args.kwargs["recent_applications"]
            is model_cls.call_args.kwargs["recent_applications"]
        )
        unity_cls.assert_called_once_with(
            model=model,
            application_registry=registry,
        )
        status_notifications_cls.assert_called_once_with(
            model=model,
            application_registry=registry,
        )
        items_service_cls.assert_called_once_with(model=model, window=window)
        fake_gtk.main.assert_called_once()
        backend.start.assert_called_once()
        backend.stop.assert_called_once()
        unity.start.assert_called_once()
        unity.stop.assert_called_once()
        status_notifications.start.assert_called_once()
        status_notifications.stop.assert_called_once()
        ui.start.assert_called_once()
        ui.stop.assert_called_once()
        registry.stop.assert_called_once_with()
        assert call_order == [
            "registry_refresh",
            "registry_start",
            "status_notifications_start",
            "unity_start",
            "show_all",
            "ui_start",
            "idle_add",
            "items_start",
            "applets_start",
            "registry_stop",
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
