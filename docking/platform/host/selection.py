"""Select the host once; resolve directory environment variables on each query."""

import sys
from functools import cache

from .base import HostServices


@cache
def host_services() -> HostServices:
    if sys.platform == "win32":
        from .implementations.windows.backend import WindowsHostServices

        return WindowsHostServices()
    if sys.platform.startswith(("freebsd", "openbsd")):
        from .implementations.bsd.backend import BsdHostServices

        return BsdHostServices()
    from .implementations.linux.backend import LinuxHostServices

    return LinuxHostServices()
