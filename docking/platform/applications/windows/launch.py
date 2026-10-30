"""Windows shell activation preserving exact selected application identity."""

from dataclasses import replace

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.platform.backends.windows import documents, shell

from ..desktop_launch import DesktopLaunchBackend, log
from ..launch_backend import HandleSource
from ..launched_process import LaunchOutcome
from ..types import ApplicationInfo


class WindowsApplicationLaunchBackend(DesktopLaunchBackend):
    def launch(
        self, application: ApplicationInfo, handle: HandleSource
    ) -> LaunchOutcome:
        if application.launch_target:
            return replace(
                shell.launch_application_target(application.launch_target),
                executable_path=application.executable_path,
            )
        source = handle()
        if source is None:
            return LaunchOutcome(False)
        try:
            if "!" in application.desktop_id and application.executable_path is None:
                return LaunchOutcome(
                    shell.activate_packaged_application(application.desktop_id)
                )
            return LaunchOutcome(bool(source.launch([], Gio.AppLaunchContext())))
        except GLib.Error as exc:
            log.bind(desktop_id=application.desktop_id, action="launch").warning(
                "Failed to launch Windows application: %s",
                exc,
            )
            return LaunchOutcome(False)

    def launch_uris(
        self,
        application: ApplicationInfo,
        uris: list[str],
        handle: HandleSource,
    ) -> LaunchOutcome:
        if application.launch_target:
            outcome = documents.launch_application_uris(
                application.document_target or application.launch_target,
                uris,
            )
            if outcome is not None:
                return replace(outcome, executable_path=application.executable_path)
        return super().launch_uris(application, uris, handle)
