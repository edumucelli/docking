"""Windows shell activation preserving exact selected application identity."""

from collections.abc import Iterable
from dataclasses import replace
from typing import Protocol, cast

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger, with_context
from docking.platform.applications.backend import (
    ApplicationHandleSource,
    ApplicationLaunchBackend,
    LaunchedProcess,
    LaunchOutcome,
    LaunchResult,
)
from docking.platform.applications.implementations.windows import documents
from docking.platform.applications.types import ApplicationInfo
from docking.platform.native.windows import shell

log = with_context(get_logger("application_launcher"))


class NativeApplicationHandle(Protocol):
    def launch(self, files: list[object], context: object | None) -> bool: ...
    def launch_uris(self, uris: list[str], context: object | None) -> None: ...


def _result(outcome: LaunchOutcome) -> LaunchResult:
    return LaunchResult(
        outcome.started,
        LaunchedProcess(outcome.process, outcome.executable_path)
        if outcome.started and outcome.process is not None
        else None,
    )


class WindowsApplicationLaunchBackend(ApplicationLaunchBackend):
    def launch(
        self,
        application: ApplicationInfo,
        handle: ApplicationHandleSource | None = None,
    ) -> LaunchResult:
        return _result(self._activate(application, handle))

    def _activate(
        self, application: ApplicationInfo, handle: ApplicationHandleSource | None
    ) -> LaunchOutcome:
        if application.launch_target:
            return replace(
                shell.launch_application_target(application.launch_target),
                executable_path=application.executable_path,
            )
        source = handle() if handle is not None else None
        if source is None:
            return LaunchOutcome(False)
        try:
            if "!" in application.desktop_id and application.executable_path is None:
                return LaunchOutcome(
                    shell.activate_packaged_application(application.desktop_id)
                )
            return LaunchOutcome(
                bool(
                    cast(NativeApplicationHandle, source).launch(
                        [], Gio.AppLaunchContext()
                    )
                )
            )
        except GLib.Error as exc:
            log.bind(desktop_id=application.desktop_id, action="launch").warning(
                "Failed to launch Windows application: %s", exc
            )
            return LaunchOutcome(False)

    def launch_action(
        self,
        application: ApplicationInfo,
        action_id: str,
        handle: ApplicationHandleSource,
    ) -> LaunchResult:
        # Win32 AppInfo supplies no desktop-file action Exec lines. Keep the
        # shared action-versus-base-activation policy informed of this failure.
        return LaunchResult(False)

    def launch_app_uris(
        self,
        application: ApplicationInfo,
        uris: Iterable[str],
        handle: ApplicationHandleSource,
    ) -> LaunchResult:
        launchable = list(uris)
        if not launchable:
            return LaunchResult(False)
        if application.launch_target:
            outcome = documents.launch_application_uris(
                application.document_target or application.launch_target, launchable
            )
            if outcome is not None:
                return _result(
                    replace(outcome, executable_path=application.executable_path)
                )
        if not application.has_gio_source:
            return LaunchResult(False)
        source = handle()
        if source is None:
            return LaunchResult(False)
        try:
            cast(NativeApplicationHandle, source).launch_uris(launchable, None)
        except GLib.Error as exc:
            log.bind(
                desktop_id=application.desktop_id, action="launch_app_uris"
            ).warning(
                "Failed to open URI list with %s: %s", application.desktop_id, exc
            )
            return LaunchResult(False)
        return LaunchResult(True)

    def launch_listing(self, listing_key: str, handle: object | None) -> LaunchResult:
        if handle is None:
            return LaunchResult(False)
        try:
            cast(NativeApplicationHandle, handle).launch([], None)
        except GLib.Error as exc:
            log.bind(listing_key=listing_key, action="launch_listing").warning(
                "Failed to launch application listing %s: %s", listing_key, exc
            )
            return LaunchResult(False)
        return LaunchResult(True)
