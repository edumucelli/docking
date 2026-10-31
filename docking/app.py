# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Application bootstrap for the Docking process.

What this module owns

This file is the narrow runtime bridge between "Python package on disk" and
"running GTK dock on screen". It performs the small set of process-wide steps
that must happen in a specific order before the rest of the dock can operate.

Boot order matters here

Several actions in this module are deliberately early and global:

1. enable ``faulthandler`` so hard crashes still produce Python tracebacks,
2. add the vendor dependency directory when running from packaged installs,
3. initialize gettext before importing modules that declare translated labels,
4. require the GTK version before importing ``gi.repository`` objects,
5. construct the core model / platform adapters / renderer / window objects,
6. hand control to ``Gtk.main()``.

Why this module should stay small

Most behavior does not belong here. Once the initial process wiring is done,
responsibility moves outward:

- configuration and theme loading go through ``docking.core``,
- environment tweaks and window tracking go through ``docking.platform``,
- GTK assembly goes through ``docking.ui.factory`` and related UI modules,
- applet lifecycle is delegated to the model.

That separation is important because entrypoints tend to become accidental
"god modules" when they start absorbing policy decisions. This file should
continue to answer only one question: how does a Docking process start and stop
cleanly?
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from typing import TYPE_CHECKING

from docking.platform.host.selection import host_services

host_services().prepare_process()

# i18n must init before any module with translatable strings is imported.
from docking.i18n import init as _init_i18n
from docking.log import get_logger, with_context

_init_i18n()

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

# Give GTK / Mutter a stable program name so the GNOME Shell extension
# can find the dock window on Wayland (where WM_CLASS is not forwarded).
GLib.set_prgname("Docking")

from docking.applets.platform import AppletPlatformServices
from docking.applets.services import AppletServices
from docking.core.config import Config, PinnedEntry, build_initial_pinned
from docking.core.theme import Theme
from docking.ipc import DockItemsService
from docking.platform.applications.identity import (
    LaunchProvenanceStore,
    ProcessIdentityService,
)
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.recents import (
    RecentApplications,
    RecentApplicationsPersistence,
)
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.composition.base import PlatformProvider
from docking.platform.composition.selection import platform_provider
from docking.platform.environment import apply_tweaks, detect_desktop
from docking.platform.icons import IconLoader
from docking.platform.model import DockModel
from docking.platform.targets import TargetService
from docking.platform.tray.notifications import StatusNotifierNotificationBridge
from docking.platform.tray.service import TrayService
from docking.platform.unity import UnityLauncherListener
from docking.ui.factory import build_dock_window
from docking.ui.renderer import DockRenderer

if TYPE_CHECKING:
    from docking.platform.backends.base import SessionBackend

_FORCE_QUIT_SOURCE_ID = 0


log = with_context(get_logger(name="app"), action="start_runtime")


def _initial_pinned_for_registry(
    registry: ApplicationRegistry,
) -> list[PinnedEntry]:
    def default_desktop_id_for(content_type: str) -> str | None:
        application = registry.default_for_content_type(content_type).listing
        return application.desktop_id if application is not None else None

    return build_initial_pinned(
        desktop_id_exists=lambda desktop_id: registry.get(desktop_id) is not None,
        default_desktop_id_for=default_desktop_id_for,
    )


def _refresh_initial_registry(registry: ApplicationRegistry) -> None:
    """Require one successfully published registry generation before config."""
    registry.refresh()
    if registry.generation <= 0:
        raise RuntimeError("Initial application discovery failed")


def _safe_stop(name: str, callback: Callable[[], object]) -> None:
    """Run one cleanup callback without preventing later cleanup."""
    try:
        callback()
    except Exception:
        log.exception("Failed to stop runtime stage: %s", name)


def main(provider: PlatformProvider | None = None) -> None:
    """Entry point for the docking application."""
    provider = platform_provider() if provider is None else provider
    apply_tweaks(desktop=detect_desktop())

    with ExitStack() as providers, ExitStack() as cleanup:
        registry = ApplicationRegistry(
            catalog_backend=provider.catalog(),
            association_backend=provider.associations(),
            registration_backend=provider.registrations(),
        )
        providers.callback(_safe_stop, "registry", registry.stop)

        provenance_store = LaunchProvenanceStore()
        process_identity_service = ProcessIdentityService(
            provenance_store, backend=provider.processes()
        )
        application_launcher = ApplicationLauncher(
            registry, provenance_store, backend=provider.launches()
        )
        icon_loader = IconLoader()
        target_service = TargetService(
            icon_loader=icon_loader, application_registry=registry
        )

        _refresh_initial_registry(registry)

        config = Config.load(
            initial_pinned_factory=lambda: _initial_pinned_for_registry(registry)
        )
        recent_applications = RecentApplications(
            registry,
            RecentApplicationsPersistence(config),
        )
        theme = Theme.load(name=config.theme, icon_size=config.icon_size).with_opacity(
            config.transparency
        )
        status_service = TrayService(backend_factory=provider.tray_backend)
        providers.callback(_safe_stop, "tray", status_service.stop)
        applet_platform = AppletPlatformServices(
            backends=provider.applet_backends(), tray=status_service
        )
        applet_services = AppletServices()
        model = DockModel(
            config=config,
            applet_services=applet_services,
            applet_platform=applet_platform,
            application_registry=registry,
            application_launcher=application_launcher,
            icon_loader=icon_loader,
            target_service=target_service,
            recent_applications=recent_applications,
        )
        cleanup.callback(_safe_stop, "applets", model.stop_applets)

        renderer = DockRenderer()
        backend = provider.session(
            config=config,
            model=model,
            application_registry=registry,
            process_identity_service=process_identity_service,
        )
        providers.callback(_safe_stop, "backend", backend.stop)

        applet_services = replace(
            applet_services,
            desktop_actions=backend.desktop_actions,
            workspaces=backend.workspaces,
            window_picker=backend.window_picker,
            idle=backend.idle,
            screen_capture=backend.screen_capture,
        )
        model.set_applet_services(applet_services)
        unity = UnityLauncherListener(
            model=model,
            application_registry=registry,
        )
        cleanup.callback(_safe_stop, "unity", unity.stop)

        status_notifications = StatusNotifierNotificationBridge(
            model=model,
            application_registry=registry,
            service=status_service,
        )
        cleanup.callback(
            _safe_stop,
            "status notifications",
            status_notifications.stop,
        )

        ui = build_dock_window(
            search_integration=provider.search(),
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=backend.windows,
            preview_service=backend.previews,
            surface_service=backend.surface,
            visibility_service=backend.visibility,
            session_backend=backend,
            application_registry=registry,
            application_launcher=application_launcher,
            icon_loader=icon_loader,
            target_service=target_service,
        )
        cleanup.callback(_safe_stop, "UI", ui.stop)

        window = ui.window
        items_service = DockItemsService(model=model, window=window)
        cleanup.callback(_safe_stop, "items service", items_service.stop)

        applet_services = replace(
            applet_services,
            search=ui.search,
            status_notifier=status_service,
        )
        model.set_applet_services(applet_services)

        # Graceful shutdown on SIGINT/SIGTERM
        cleanup.callback(provider.desktop().install_quit_handlers(_quit))

        registry.start()
        status_service.start()
        status_notifications.start()
        unity.start()
        window.show_all()
        ui.start()
        GLib.idle_add(_start_runtime, items_service, model, backend)
        Gtk.main()


def _start_runtime(
    items_service: DockItemsService,
    model: DockModel,
    backend: SessionBackend,
) -> bool:
    """Start background runtime pieces after the window has been shown."""
    _start_runtime_stage(name="backend", start=backend.start)
    _start_runtime_stage(name="items_service", start=items_service.start)
    _start_runtime_stage(name="applets", start=model.start_applets)
    return False


def _start_runtime_stage(*, name: str, start: Callable[[], object]) -> None:
    try:
        start()
    except Exception:
        log.exception("Failed to start runtime stage: %s", name)


def _quit() -> bool:
    global _FORCE_QUIT_SOURCE_ID

    # Shutdown is intentionally two-stage.  Give GTK, applets, IPC services,
    # and backend adapters a normal main-loop exit first; if something wedges
    # during teardown, force the process out after a short grace period.
    if _FORCE_QUIT_SOURCE_ID == 0:
        # GDK must cancel its prepared Wayland read before gtk_main() performs
        # the final display roundtrip. Quitting directly from a high-priority
        # signal source can otherwise deadlock in gdk_flush().
        GLib.idle_add(Gtk.main_quit)
        _FORCE_QUIT_SOURCE_ID = GLib.timeout_add_seconds(3, _force_quit)
    return False


def _force_quit() -> bool:
    log.warning("Forcing shutdown after the cleanup timeout")
    os._exit(0)


if __name__ == "__main__":
    main()
