"""The single host-family decision for runtime composition."""

from functools import cache

from docking.platform.host.base import HostFamily
from docking.platform.host.selection import host_services

from .base import PlatformProvider


@cache
def platform_provider() -> PlatformProvider:
    if host_services().family is HostFamily.BSD:
        from .bsd import BsdPlatformProvider

        return BsdPlatformProvider()
    from .linux import LinuxPlatformProvider

    return LinuxPlatformProvider()
