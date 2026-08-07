"""Compatibility facade over canonical application, icon, and target services."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib

from docking.platform import icons, process_identity, targets
from docking.platform.applications import entries as desktop_entries
from docking.platform.applications.constants import (
    DESKTOP_SUFFIX,
    FALLBACK_ICON,
    GNOME_APP_PREFIX,
)
from docking.platform.applications.identity import LaunchProvenanceStore
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.projections import dock_metadata
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.types import ApplicationInfo
from docking.platform.environment import flatpak

DEFAULT_XDG_DATA_DIRS = desktop_entries.DEFAULT_XDG_DATA_DIRS
FILE_ICON_CACHE_MAX_ENTRIES = icons.FILE_ICON_CACHE_MAX_ENTRIES
SNAP_XDG_DATA_DIR = desktop_entries.SNAP_XDG_DATA_DIR
HOST_XDG_DATA_DIRS = icons.HOST_XDG_DATA_DIRS
HOST_PIXMAP_DIRS = icons.HOST_PIXMAP_DIRS
HOST_FILESYSTEM_ROOT = icons.HOST_FILESYSTEM_ROOT
ICON_FILE_EXTENSIONS = icons.ICON_FILE_EXTENSIONS
IconLoader = icons.IconLoader
fallback_file_icon_name = icons.fallback_file_icon_name
_host_icon_file_candidates = icons._host_icon_file_candidates
_create_icon_theme = icons._create_icon_theme
_theme_icon_candidates = icons._theme_icon_candidates
FileTargetInfo = targets.FileTargetInfo
normalize_file_target = targets.normalize_file_target
open_target = targets.open_target


def _legacy_desktop_info(
    application: ApplicationInfo,
) -> desktop_entries.DesktopInfo:
    """Project canonical metadata onto the historical DesktopInfo tuple."""
    return desktop_entries.DesktopInfo(*dock_metadata(application))


class _CompatibilityProvenanceStore(LaunchProvenanceStore):
    """Record legacy launches in the process-identity compatibility service."""

    def record_launch(
        self,
        *,
        process: Any,
        desktop_id: str,
        executable_path: Path | None,
    ) -> None:
        process_identity.record_launch(
            process=process,
            desktop_id=desktop_id,
            executable_path=executable_path,
        )


class Launcher:
    """Legacy combined service backed by one canonical application registry."""

    def __init__(
        self,
        registry: ApplicationRegistry | None = None,
        *,
        application_launcher: ApplicationLauncher | None = None,
        icon_loader: IconLoader | None = None,
        target_service: targets.TargetService | None = None,
    ) -> None:
        self._owns_registry = registry is None
        self._registry = registry if registry is not None else ApplicationRegistry()
        if self._owns_registry:
            self._registry.refresh()

        if icon_loader is None:
            icon_loader = (
                target_service.icon_loader
                if target_service is not None
                else IconLoader(
                    normalize_file_target=lambda target: targets.normalize_file_target(
                        target
                    )
                )
            )
        self._icon_loader = icon_loader
        self._target_service = (
            target_service
            if target_service is not None
            else targets.TargetService(icon_loader=icon_loader)
        )
        self._application_launcher = (
            application_launcher
            if application_launcher is not None
            else ApplicationLauncher(
                registry=self._registry,
                provenance_store=_CompatibilityProvenanceStore(),
            )
        )

    @property
    def registry(self) -> ApplicationRegistry:
        """Return the registry owned or borrowed by this facade."""
        return self._registry

    @property
    def application_launcher(self) -> ApplicationLauncher:
        """Return the canonical launcher used by application operations."""
        return self._application_launcher

    @property
    def _icon_cache(self) -> dict[tuple[str, int], GdkPixbuf.Pixbuf | None]:
        return self._icon_loader._icon_cache

    @property
    def _file_icon_cache(
        self,
    ) -> dict[tuple[str, int, int, int], GdkPixbuf.Pixbuf | None]:
        return self._icon_loader._file_icon_cache

    def resolve(
        self,
        desktop_id: str,
        *,
        log_failures: bool = True,
    ) -> desktop_entries.DesktopInfo | None:
        if self._owns_registry:
            self._registry.refresh()
        application = self._registry.resolve(
            desktop_id,
            log_failures=log_failures,
        )
        return _legacy_desktop_info(application) if application is not None else None

    def resolve_by_wm_class(
        self,
        wm_class: str,
    ) -> desktop_entries.DesktopInfo | None:
        application = self._registry.resolve_by_wm_class(wm_class)
        return _legacy_desktop_info(application) if application is not None else None

    def resolve_all_by_wm_class(
        self,
        wm_class: str,
    ) -> tuple[desktop_entries.DesktopInfo, ...]:
        return tuple(
            _legacy_desktop_info(application)
            for application in self._registry.resolve_all_by_wm_class(wm_class)
        )

    def resolve_by_executable_path(
        self,
        executable_path: Path,
    ) -> desktop_entries.DesktopInfo | None:
        application = self._registry.resolve_by_executable_path(executable_path)
        return _legacy_desktop_info(application) if application is not None else None

    def refresh_desktop_entries(self) -> None:
        self._registry.refresh_desktop_entries()

    def get_actions(self, desktop_id: str) -> list[desktop_entries.DesktopAction]:
        return self._application_launcher.get_actions(desktop_id)

    def launch(self, desktop_id: str) -> bool:
        return self._application_launcher.launch(desktop_id)

    def launch_action(self, desktop_id: str, action_id: str) -> bool:
        return self._application_launcher.launch_action(desktop_id, action_id)

    def launch_new_window(self, desktop_id: str) -> bool:
        return self._application_launcher.launch_new_window(desktop_id)

    def load_icon(self, icon_name: str, size: int) -> GdkPixbuf.Pixbuf | None:
        return self._icon_loader.load_icon(icon_name=icon_name, size=size)

    def load_icon_file(self, path: Path, size: int) -> GdkPixbuf.Pixbuf | None:
        return self._icon_loader.load_icon_file(path=path, size=size)

    def load_desktop_icon(
        self,
        info: desktop_entries.DesktopInfo | ApplicationInfo,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        return self._icon_loader.load_desktop_icon(info=info, size=size)

    def load_gicon(
        self,
        gicon: Gio.Icon | None,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        return self._icon_loader.load_gicon(gicon=gicon, size=size)

    def resolve_file(self, target: str, size: int) -> FileTargetInfo | None:
        return self._target_service.resolve_file(target=target, size=size)

    def resolve_file_icon(
        self,
        *,
        target: str,
        gicon: Gio.Icon | None,
        content_type: str,
        size: int,
        is_dir: bool,
    ) -> GdkPixbuf.Pixbuf | None:
        return self._target_service.resolve_file_icon(
            target=target,
            gicon=gicon,
            content_type=content_type,
            size=size,
            is_dir=is_dir,
        )

    def _load_cached_file_icon(
        self,
        *,
        path: Path,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        return self._icon_loader._load_cached_file_icon(path=path, size=size)

    def default_directory_app_name(self) -> str | None:
        return self._target_service.default_directory_app_name()

    def _try_load_gicon(
        self,
        gicon: Gio.Icon,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        return self._icon_loader._try_load_gicon(gicon=gicon, size=size)

    def _try_load_icon(
        self,
        icon_name: str,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        return self._icon_loader._try_load_icon(icon_name=icon_name, size=size)

    def _try_load_icon_without_fallback(
        self,
        icon_name: str,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        return self._icon_loader._try_load_icon_without_fallback(
            icon_name=icon_name,
            size=size,
        )

    def _try_load_fallback_icon(self, *, size: int) -> GdkPixbuf.Pixbuf | None:
        return self._icon_loader._try_load_fallback_icon(size=size)


_application_launcher_lock = RLock()
_configured_application_launcher: ApplicationLauncher | None = None
_standalone_application_launcher: ApplicationLauncher | None = None
_standalone_registry: ApplicationRegistry | None = None


def configure_application_launcher(
    application_launcher: ApplicationLauncher,
) -> ApplicationLauncher | None:
    """Install one launcher for legacy free-function consumers."""
    global _configured_application_launcher
    with _application_launcher_lock:
        previous = _configured_application_launcher
        _configured_application_launcher = application_launcher
        return previous


def reset_application_launcher(
    previous: ApplicationLauncher | None = None,
) -> None:
    """Restore a previous configured launcher, or standalone fallback mode."""
    global _configured_application_launcher
    with _application_launcher_lock:
        _configured_application_launcher = previous


def get_configured_application_launcher() -> ApplicationLauncher | None:
    """Return the explicitly configured launcher, excluding the fallback."""
    with _application_launcher_lock:
        return _configured_application_launcher


def _selected_application_launcher() -> ApplicationLauncher:
    global _standalone_application_launcher, _standalone_registry
    with _application_launcher_lock:
        if _configured_application_launcher is not None:
            return _configured_application_launcher
        if _standalone_application_launcher is None:
            _standalone_registry = ApplicationRegistry()
            _standalone_application_launcher = ApplicationLauncher(
                registry=_standalone_registry,
                provenance_store=_CompatibilityProvenanceStore(),
            )
        assert _standalone_registry is not None
        _standalone_registry.refresh()
        return _standalone_application_launcher


def get_actions(desktop_id: str) -> list[desktop_entries.DesktopAction]:
    return _selected_application_launcher().get_actions(desktop_id)


def launch_action(desktop_id: str, action_id: str) -> None:
    _selected_application_launcher().launch_action(desktop_id, action_id)


def launch_new_window(desktop_id: str) -> None:
    _selected_application_launcher().launch_new_window(desktop_id)


def launch(desktop_id: str) -> None:
    _selected_application_launcher().launch(desktop_id)


configure = configure_application_launcher
reset = reset_application_launcher

__all__ = [
    "DEFAULT_XDG_DATA_DIRS",
    "DESKTOP_SUFFIX",
    "FALLBACK_ICON",
    "FILE_ICON_CACHE_MAX_ENTRIES",
    "GNOME_APP_PREFIX",
    "HOST_FILESYSTEM_ROOT",
    "HOST_PIXMAP_DIRS",
    "HOST_XDG_DATA_DIRS",
    "ICON_FILE_EXTENSIONS",
    "SNAP_XDG_DATA_DIR",
    "FileTargetInfo",
    "GLib",
    "IconLoader",
    "Launcher",
    "configure",
    "configure_application_launcher",
    "desktop_entries",
    "fallback_file_icon_name",
    "flatpak",
    "get_actions",
    "get_configured_application_launcher",
    "launch",
    "launch_action",
    "launch_new_window",
    "normalize_file_target",
    "open_target",
    "reset",
    "reset_application_launcher",
]
