"""Composition entry point for the narrow desktop facade."""

from functools import cache

from docking.platform.host.base import HostFamily
from docking.platform.host.selection import host_services

from .base import DesktopServices


@cache
def desktop_services() -> DesktopServices:
    if host_services().family is HostFamily.WINDOWS:
        from .implementations.windows.backend import WindowsDesktopServices

        return WindowsDesktopServices()
    from .implementations.unix.backend import UnixDesktopServices

    return UnixDesktopServices()
