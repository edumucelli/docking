"""Select the host once; resolve directory environment variables on each query."""

from functools import cache

from .base import HostServices


@cache
def host_services() -> HostServices:
    # Environment's XDG helpers import host_services, so resolve this lazily.
    from docking.platform.environment import is_bsd, is_windows

    if is_windows():
        from .implementations.windows.backend import WindowsHostServices

        return WindowsHostServices()
    if is_bsd():
        from .implementations.bsd.backend import BsdHostServices

        return BsdHostServices()
    from .implementations.linux.backend import LinuxHostServices

    return LinuxHostServices()
