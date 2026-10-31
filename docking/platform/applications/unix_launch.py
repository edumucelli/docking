"""Unix application activation through desktop Exec and Gio metadata."""

from .desktop_launch import DesktopLaunchBackend
from .launch_backend import HandleSource
from .launched_process import LaunchOutcome
from .types import ApplicationInfo


class UnixApplicationLaunchBackend(DesktopLaunchBackend):
    def launch(
        self, application: ApplicationInfo, _handle: HandleSource
    ) -> LaunchOutcome:
        return self._launch_exec_line(
            application=application,
            exec_line=application.exec_line,
            action="launch",
        )
