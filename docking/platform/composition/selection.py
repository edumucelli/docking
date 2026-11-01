"""The single host-family decision for runtime composition."""

from functools import cache

from docking.platform.host.base import HostFamily
from docking.platform.host.selection import host_services

from .base import PlatformProvider


@cache
def platform_provider() -> PlatformProvider:
    family = host_services().family
    if family is HostFamily.WINDOWS:
        from .windows import WindowsPlatformProvider

        return WindowsPlatformProvider()
    if family is HostFamily.BSD:
        from .bsd import BsdPlatformProvider

        return BsdPlatformProvider()
    from .linux import LinuxPlatformProvider

    return LinuxPlatformProvider()
