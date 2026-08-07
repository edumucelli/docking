"""Tests for the legacy Launcher facade over canonical services."""

from __future__ import annotations

from pathlib import Path
from typing import get_type_hints
from unittest.mock import MagicMock

from docking.platform import icons as icons_mod
from docking.platform import launcher as launcher_mod
from docking.platform import targets as targets_mod
from docking.platform.applications.constants import (
    DESKTOP_SUFFIX,
    FALLBACK_ICON,
    GNOME_APP_PREFIX,
)
from docking.platform.applications.entries import DesktopAction, DesktopInfo
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)
from docking.platform.launcher import Launcher


def _application(
    desktop_id: str = "org.example.App.desktop",
) -> ApplicationInfo:
    return ApplicationInfo(
        desktop_id=desktop_id,
        name="Example App",
        declared_icon="example",
        wm_class="ExampleApp",
        exec_line="/usr/bin/example",
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.SANDBOX,
        desktop_file=Path(f"/tmp/{desktop_id}"),
        executable_path=Path("/usr/bin/example"),
        aliases=("exampleapp",),
        visible=True,
        has_gio_source=False,
    )


def _facade(
    *,
    registry: MagicMock | None = None,
    application_launcher: MagicMock | None = None,
) -> tuple[Launcher, MagicMock, MagicMock, MagicMock, MagicMock]:
    registry = registry or MagicMock()
    application_launcher = application_launcher or MagicMock()
    icon_loader = MagicMock()
    target_service = MagicMock()
    target_service.icon_loader = icon_loader
    facade = Launcher(
        registry=registry,
        application_launcher=application_launcher,
        icon_loader=icon_loader,
        target_service=target_service,
    )
    return facade, registry, application_launcher, icon_loader, target_service


def test_no_injection_owns_and_refreshes_one_registry(monkeypatch):
    registry = MagicMock()
    application_launcher = MagicMock()
    icon_loader = MagicMock()
    target_service = MagicMock()
    monkeypatch.setattr(
        launcher_mod,
        "ApplicationRegistry",
        MagicMock(return_value=registry),
    )
    monkeypatch.setattr(
        launcher_mod,
        "ApplicationLauncher",
        MagicMock(return_value=application_launcher),
    )
    monkeypatch.setattr(
        launcher_mod,
        "IconLoader",
        MagicMock(return_value=icon_loader),
    )
    monkeypatch.setattr(
        launcher_mod.targets,
        "TargetService",
        MagicMock(return_value=target_service),
    )

    facade = Launcher()

    assert facade.registry is registry
    assert facade.application_launcher is application_launcher
    registry.refresh.assert_called_once_with()
    launcher_mod.ApplicationLauncher.assert_called_once()
    assert launcher_mod.ApplicationLauncher.call_args.kwargs["registry"] is registry


def test_injected_registry_is_borrowed_without_refresh():
    facade, registry, application_launcher, _, _ = _facade()

    assert facade.registry is registry
    assert facade.application_launcher is application_launcher
    facade.resolve("example.desktop")
    registry.refresh.assert_not_called()


def test_owned_resolve_refreshes_before_direct_lookup(monkeypatch):
    application = _application()
    registry = MagicMock()
    registry.resolve.return_value = application
    monkeypatch.setattr(
        launcher_mod,
        "ApplicationRegistry",
        MagicMock(return_value=registry),
    )
    facade = Launcher(
        application_launcher=MagicMock(),
        icon_loader=MagicMock(),
        target_service=MagicMock(),
    )
    registry.refresh.reset_mock()

    assert facade.resolve(application.desktop_id) is not None

    registry.refresh.assert_called_once_with()
    registry.resolve.assert_called_once_with(
        application.desktop_id,
        log_failures=True,
    )


def test_resolution_projects_only_canonical_registry_metadata():
    application = _application()
    registry = MagicMock()
    registry.resolve.return_value = application
    registry.resolve_by_wm_class.return_value = application
    registry.resolve_all_by_wm_class.return_value = (application,)
    registry.resolve_by_executable_path.return_value = application
    facade, _, _, _, _ = _facade(registry=registry)
    expected = DesktopInfo(
        application.desktop_id,
        application.name,
        application.declared_icon,
        application.wm_class,
        application.exec_line,
    )

    assert facade.resolve(application.desktop_id) == expected
    assert facade.resolve_by_wm_class("ExampleApp") == expected
    assert facade.resolve_all_by_wm_class("ExampleApp") == (expected,)
    assert facade.resolve_by_executable_path(Path("/usr/bin/example")) == expected


def test_unresolved_application_returns_none():
    facade, registry, _, _, _ = _facade()
    registry.resolve.return_value = None
    registry.resolve_by_wm_class.return_value = None
    registry.resolve_by_executable_path.return_value = None

    assert facade.resolve("missing.desktop") is None
    assert facade.resolve_by_wm_class("missing") is None
    assert facade.resolve_by_executable_path(Path("/missing")) is None


def test_application_operations_delegate_to_application_launcher():
    facade, _, application_launcher, _, _ = _facade()
    application_launcher.get_actions.return_value = [
        DesktopAction("new-window", "New Window")
    ]
    application_launcher.launch.return_value = True
    application_launcher.launch_action.return_value = True
    application_launcher.launch_new_window.return_value = True

    assert facade.get_actions("example.desktop") == [
        DesktopAction("new-window", "New Window")
    ]
    assert facade.launch("example.desktop") is True
    assert facade.launch_action("example.desktop", "private") is True
    assert facade.launch_new_window("example.desktop") is True

    application_launcher.get_actions.assert_called_once_with("example.desktop")
    application_launcher.launch.assert_called_once_with("example.desktop")
    application_launcher.launch_action.assert_called_once_with(
        "example.desktop", "private"
    )
    application_launcher.launch_new_window.assert_called_once_with("example.desktop")


def test_refresh_delegates_to_registry():
    facade, registry, _, _, _ = _facade()

    facade.refresh_desktop_entries()

    registry.refresh_desktop_entries.assert_called_once_with()


def test_icon_and_target_operations_delegate_without_own_caches():
    facade, _, _, icon_loader, target_service = _facade()
    icon = object()
    target = object()
    icon_loader.load_icon.return_value = icon
    icon_loader.load_icon_file.return_value = icon
    icon_loader.load_desktop_icon.return_value = icon
    icon_loader.load_gicon.return_value = icon
    target_service.resolve_file.return_value = target
    target_service.resolve_file_icon.return_value = icon
    target_service.default_directory_app_name.return_value = "Files"
    info = DesktopInfo("example.desktop", "Example", "example", "Example", "example")

    assert facade.load_icon("example", 48) is icon
    assert facade.load_icon_file(Path("/tmp/example.svg"), 48) is icon
    assert facade.load_desktop_icon(info, 48) is icon
    assert facade.load_gicon(None, 48) is icon
    assert facade.resolve_file("/tmp/example", 48) is target
    assert (
        facade.resolve_file_icon(
            target="/tmp/example",
            gicon=None,
            content_type="text/plain",
            size=48,
            is_dir=False,
        )
        is icon
    )
    assert facade.default_directory_app_name() == "Files"
    assert facade._icon_cache is icon_loader._icon_cache
    assert facade._file_icon_cache is icon_loader._file_icon_cache


def test_module_level_operations_use_configured_canonical_launcher():
    application_launcher = MagicMock()
    registry = MagicMock()
    application_launcher.registry = registry
    application_launcher.get_actions.return_value = []
    application_launcher.launch.return_value = True
    application_launcher.launch_action.return_value = True
    application_launcher.launch_new_window.return_value = True
    previous = launcher_mod.configure_application_launcher(application_launcher)
    try:
        assert launcher_mod.get_actions("example.desktop") == []
        assert launcher_mod.launch("example.desktop") is None
        assert launcher_mod.launch_action("example.desktop", "private") is None
        assert launcher_mod.launch_new_window("example.desktop") is None
    finally:
        launcher_mod.reset_application_launcher(previous)
    registry.refresh.assert_not_called()
    application_launcher.launch.assert_called_once_with("example.desktop")
    application_launcher.launch_action.assert_called_once_with(
        "example.desktop",
        "private",
    )
    application_launcher.launch_new_window.assert_called_once_with("example.desktop")


def test_module_level_launch_functions_keep_legacy_none_annotations():
    for function in (
        launcher_mod.launch,
        launcher_mod.launch_action,
        launcher_mod.launch_new_window,
    ):
        assert get_type_hints(function)["return"] is type(None)


def test_standalone_module_functions_refresh_before_every_operation(monkeypatch):
    registry = MagicMock()
    application_launcher = MagicMock()
    application_launcher.get_actions.return_value = []
    application_launcher.launch.return_value = True
    application_launcher.launch_action.return_value = True
    application_launcher.launch_new_window.return_value = True
    monkeypatch.setattr(
        launcher_mod,
        "ApplicationRegistry",
        MagicMock(return_value=registry),
    )
    monkeypatch.setattr(
        launcher_mod,
        "ApplicationLauncher",
        MagicMock(return_value=application_launcher),
    )
    monkeypatch.setattr(launcher_mod, "_standalone_registry", None)
    monkeypatch.setattr(launcher_mod, "_standalone_application_launcher", None)
    launcher_mod.reset_application_launcher()

    assert launcher_mod.get_actions("example.desktop") == []
    assert launcher_mod.launch("example.desktop") is None
    assert launcher_mod.launch_action("example.desktop", "private") is None
    assert launcher_mod.launch_new_window("example.desktop") is None

    launcher_mod.ApplicationRegistry.assert_called_once_with()
    assert registry.refresh.call_count == 4
    launcher_mod.ApplicationLauncher.assert_called_once()


def test_public_constants_and_target_helpers_are_canonical_aliases():
    assert launcher_mod.DESKTOP_SUFFIX is DESKTOP_SUFFIX
    assert launcher_mod.FALLBACK_ICON is FALLBACK_ICON
    assert launcher_mod.GNOME_APP_PREFIX is GNOME_APP_PREFIX
    assert launcher_mod.IconLoader is icons_mod.IconLoader
    assert launcher_mod.FileTargetInfo is targets_mod.FileTargetInfo
    assert launcher_mod.normalize_file_target is targets_mod.normalize_file_target
    assert launcher_mod.open_target is targets_mod.open_target


def test_facade_has_no_desktop_discovery_or_metadata_indexes():
    facade, _, _, _, _ = _facade()

    assert not hasattr(facade, "_desktop_dirs")
    assert not hasattr(facade, "_wm_class_index")
    assert not hasattr(facade, "_executable_path_index")
