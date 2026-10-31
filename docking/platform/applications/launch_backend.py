"""Selected-application launch contract and platform selection."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any, Protocol

from docking.platform import environment

from .launched_process import LaunchOutcome
from .types import ApplicationAction, ApplicationInfo

# Gio handles remain private to the registry and are obtained only on demand.
HandleSource = Callable[[], Any | None]
PopenFactory = Callable[..., subprocess.Popen[Any]]


class ApplicationLaunchBackend(Protocol):
    def launch(
        self, application: ApplicationInfo, handle: HandleSource, /
    ) -> LaunchOutcome: ...
    def launch_action(
        self,
        application: ApplicationInfo,
        action_id: str,
        action: ApplicationAction | None,
        handle: HandleSource,
    ) -> LaunchOutcome: ...
    def launch_uris(
        self,
        application: ApplicationInfo,
        uris: list[str],
        handle: HandleSource,
    ) -> LaunchOutcome: ...
    def launch_listing(self, listing_key: str, handle: Any) -> bool: ...


def create_launch_backend(
    *, popen: PopenFactory | None = None
) -> ApplicationLaunchBackend:
    if environment.is_windows():
        from .windows.launch import WindowsApplicationLaunchBackend

        return WindowsApplicationLaunchBackend(popen=popen)
    from .unix_launch import UnixApplicationLaunchBackend

    return UnixApplicationLaunchBackend(popen=popen)
