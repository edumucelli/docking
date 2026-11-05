"""Compose native acceptance services after the candidate runtime is selected."""

from __future__ import annotations

import atexit
import ctypes
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.platform.applications.backend import ApplicationSources
    from docking.platform.applications.registry import ApplicationRegistry


def create_application_registry(
    *, sources: ApplicationSources | None = None
) -> ApplicationRegistry:
    # Keep runtime imports inside the action: installed-candidate validation
    # must run before any Docking module is selected by a desktop tool.
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.composition.selection import platform_provider

    if sources is None:
        sources = platform_provider().application_sources()
    return ApplicationRegistry(
        catalog_backend=sources.catalog,
        association_backend=sources.associations,
        registration_backend=sources.registrations,
    )


def ready_application_registry() -> ApplicationRegistry:
    """Own a one-shot tool's catalog and wait for its first complete snapshot."""
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    from tools.windows.visual.applications_domain_smoke import (
        refresh_association_registry,
    )
    from tools.windows.visual.desktop_driver import WindowsDesktop

    registry = create_application_registry()
    alertable = ctypes.WinDLL("kernel32", use_last_error=True).SleepEx
    alertable.argtypes = (ctypes.c_uint32, ctypes.c_int32)
    alertable.restype = ctypes.c_uint32
    context, observed = GLib.MainContext.default(), {}

    def ready() -> bool:
        refresh_association_registry(registry, context, alertable, observed)
        return not registry.initial_discovery_pending

    try:
        registry.start()
        WindowsDesktop.wait_for(
            ready, "Native application catalog did not become ready", seconds=60
        )
    except BaseException:
        registry.stop()
        raise
    # These legacy tools own the registry for the lifetime of their process.
    # Explicit stop in a tool's finally block remains safe and idempotent.
    atexit.register(registry.stop)
    return registry
