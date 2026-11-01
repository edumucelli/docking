"""Registry-backed launching for selected desktop applications."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, cast

import gi

from docking.platform.applications.backend import ApplicationLaunchBackend

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.log import get_logger, with_context
from docking.platform.environment import flatpak, is_flatpak

from ...backend import ApplicationHandleSource, LaunchResult
from ...types import ApplicationInfo, ApplicationLocation
from . import entries as desktop_entries
from .desktop_launch import DesktopExecLauncher

log = with_context(get_logger(name="application_launcher"))


class NativeApplicationHandle(Protocol):
    def launch_action(self, action_id: str, context: object | None) -> None: ...
    def launch_uris(self, uris: list[str], context: object | None) -> None: ...
    def launch(self, files: list[object], context: object | None) -> None: ...


class UnixApplicationLaunchBackend(DesktopExecLauncher, ApplicationLaunchBackend):
    """Launch registry-selected applications without publishing Gio handles."""

    def launch(
        self,
        application: ApplicationInfo,
        handle: ApplicationHandleSource | None = None,
    ) -> LaunchResult:
        """Launch a selected desktop application through its direct Exec line."""
        return self._launch_exec_line(
            application=application,
            exec_line=application.exec_line,
            action="launch",
        )

    def launch_action(
        self,
        application: ApplicationInfo,
        action_id: str,
        handle: ApplicationHandleSource,
    ) -> LaunchResult:
        """Launch a source-aware desktop action."""

        action = next(
            (
                candidate
                for candidate in application.actions
                if candidate.action_id == action_id
            ),
            None,
        )
        if (
            application.location is ApplicationLocation.SANDBOX
            and application.has_gio_source
            and (source := handle()) is not None
        ):
            try:
                cast(NativeApplicationHandle, source).launch_action(action_id, None)
            except GLib.Error as exc:
                log.bind(
                    desktop_id=application.desktop_id, action="launch_action"
                ).warning(
                    "Failed to launch action %s for %s: %s",
                    action_id,
                    application.desktop_id,
                    exc,
                )
                return LaunchResult(False)
            return LaunchResult(True)

        exec_line = action.file_exec_line if action is not None else ""
        if not exec_line and application.desktop_file is not None:
            exec_line = desktop_entries.desktop_file_action_exec(
                application.desktop_file,
                action_id,
            )
        return self._launch_exec_line(
            application=application,
            exec_line=exec_line,
            action="launch_action",
        )

    def launch_app_uris(
        self,
        application: ApplicationInfo,
        uris: Iterable[str],
        handle: ApplicationHandleSource,
    ) -> LaunchResult:
        """Open a URI list with one specifically selected application."""
        launchable = list(uris)
        if not launchable:
            return LaunchResult(False)

        if application.location is ApplicationLocation.HOST and is_flatpak():
            return self._launch_host_app_uris(
                application=application,
                uris=launchable,
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
                "Failed to open URI list with %s: %s",
                application.desktop_id,
                exc,
            )
            return LaunchResult(False)
        return LaunchResult(True)

    def _launch_host_app_uris(
        self,
        *,
        application: ApplicationInfo,
        uris: list[str],
    ) -> LaunchResult:
        """Launch host-owned desktop metadata on the host side of Flatpak."""
        desktop_file = application.desktop_file
        if desktop_file is None:
            return LaunchResult(False)
        try:
            relative = desktop_file.relative_to(desktop_entries.HOST_FILESYSTEM_ROOT)
        except ValueError:
            host_desktop_file = desktop_file
        else:
            host_desktop_file = Path("/") / relative

        argv = flatpak.host_command(["gio", "launch", str(host_desktop_file), *uris])
        if argv is None:
            log.bind(
                desktop_id=application.desktop_id,
                action="launch_app_uris",
            ).warning(
                "Cannot open URIs with host desktop file without flatpak-spawn: %s",
                desktop_file,
            )
            return LaunchResult(False)
        try:
            self._popen(
                argv,
                shell=False,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.bind(
                desktop_id=application.desktop_id,
                action="launch_app_uris",
            ).warning(
                "Failed to open URI list with host application %s: %s",
                application.desktop_id,
                exc,
            )
            return LaunchResult(False)
        return LaunchResult(True)

    def launch_listing(self, listing_key: str, handle: object | None) -> LaunchResult:
        """Launch a transient Gio-backed listing by its opaque registry token."""
        if handle is None:
            return LaunchResult(False)
        try:
            cast(NativeApplicationHandle, handle).launch([], None)
        except GLib.Error as exc:
            log.bind(listing_key=listing_key, action="launch_listing").warning(
                "Failed to launch application listing %s: %s",
                listing_key,
                exc,
            )
            return LaunchResult(False)
        return LaunchResult(True)


__all__ = ["UnixApplicationLaunchBackend"]
