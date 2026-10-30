"""Desktop-entry and Gio launch operations shared by application backends."""

from __future__ import annotations

import subprocess
from pathlib import Path
from shlex import join as shell_join
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.log import get_logger, with_context
from docking.platform import commands
from docking.platform.environment import flatpak, is_flatpak

from . import entries as desktop_entries
from .launch_backend import HandleSource, PopenFactory
from .launched_process import LaunchOutcome
from .types import ApplicationAction, ApplicationInfo, ApplicationLocation

log = with_context(get_logger(name="application_launcher"))


class DesktopLaunchBackend:
    """Handle portable desktop metadata; concrete backends choose base activation."""

    def __init__(self, *, popen: PopenFactory | None = None) -> None:
        self._popen = subprocess.Popen if popen is None else popen

    def launch_action(
        self,
        application: ApplicationInfo,
        action_id: str,
        action: ApplicationAction | None,
        handle: HandleSource,
    ) -> LaunchOutcome:
        """Launch a source-aware desktop action."""
        desktop_id = application.desktop_id
        if (
            application.location is ApplicationLocation.SANDBOX
            and application.has_gio_source
        ):
            source = handle()
            if source is not None:
                try:
                    source.launch_action(action_id, None)
                except GLib.Error as exc:
                    log.bind(desktop_id=desktop_id, action="launch_action").warning(
                        "Failed to launch action %s for %s: %s",
                        action_id,
                        desktop_id,
                        exc,
                    )
                    return LaunchOutcome(False)
                return LaunchOutcome(True)

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

    def launch_uris(
        self,
        application: ApplicationInfo,
        uris: list[str],
        handle: HandleSource,
    ) -> LaunchOutcome:
        desktop_id = application.desktop_id
        launchable = uris
        if application.location is ApplicationLocation.HOST and is_flatpak():
            return self._launch_host_app_uris(
                application=application,
                uris=launchable,
            )

        if not application.has_gio_source:
            return LaunchOutcome(False)
        source = handle()
        if source is None:
            return LaunchOutcome(False)
        try:
            source.launch_uris(launchable, None)
        except GLib.Error as exc:
            log.bind(desktop_id=desktop_id, action="launch_app_uris").warning(
                "Failed to open URI list with %s: %s",
                desktop_id,
                exc,
            )
            return LaunchOutcome(False)
        return LaunchOutcome(True)

    def _launch_host_app_uris(
        self,
        *,
        application: ApplicationInfo,
        uris: list[str],
    ) -> LaunchOutcome:
        """Launch host-owned desktop metadata on the host side of Flatpak."""
        desktop_file = application.desktop_file
        if desktop_file is None:
            return LaunchOutcome(False)
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
            return LaunchOutcome(False)
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
            return LaunchOutcome(False)
        return LaunchOutcome(True)

    def launch_listing(self, listing_key: str, handle: Any) -> bool:
        """Launch a transient Gio-backed listing by its opaque registry token."""
        try:
            handle.launch([], None)
        except GLib.Error as exc:
            log.bind(listing_key=listing_key, action="launch_listing").warning(
                "Failed to launch application listing %s: %s",
                listing_key,
                exc,
            )
            return False
        return True

    def _launch_exec_line(
        self,
        *,
        application: ApplicationInfo,
        exec_line: str,
        action: str,
    ) -> LaunchOutcome:
        if not exec_line:
            return LaunchOutcome(False)
        try:
            argv = commands.desktop_exec_argv(exec_line)
        except ValueError as exc:
            log.bind(
                desktop_id=application.desktop_id,
                action="parse_exec",
            ).warning(
                "Failed to parse launch command for %s: %s",
                application.desktop_id,
                exc,
            )
            return LaunchOutcome(False)
        if not argv:
            return LaunchOutcome(False)

        if application.terminal:
            if application.location is ApplicationLocation.SANDBOX and is_flatpak():
                terminal_argv = commands.build_terminal_argv(
                    command=shell_join(argv),
                    resolver=commands.resolve_terminal_executable,
                )
            else:
                terminal_argv = commands.build_terminal_argv(command=shell_join(argv))
            if terminal_argv is None:
                return LaunchOutcome(False)
            argv = terminal_argv

        working_directory = application.working_directory or None
        if application.location is ApplicationLocation.HOST:
            host_argv = (
                flatpak.host_command(argv, working_directory=working_directory)
                if working_directory
                else flatpak.host_command(argv)
            )
            if host_argv is None:
                log.bind(
                    desktop_id=application.desktop_id,
                    action=action,
                ).warning(
                    "Cannot launch host desktop file without flatpak-spawn: %s",
                    application.desktop_file,
                )
                return LaunchOutcome(False)
            argv = host_argv
            working_directory = None

        try:
            process = self._popen(
                argv,
                shell=False,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=working_directory,
            )
        except OSError as exc:
            log.bind(
                desktop_id=application.desktop_id,
                action=action,
            ).warning(
                "Failed to launch %s: %s",
                application.desktop_id,
                exc,
            )
            return LaunchOutcome(False)

        executable_path = (
            application.executable_path
            if exec_line == application.exec_line
            else desktop_entries.executable_path_from_exec_line(exec_line)
        )
        return LaunchOutcome(True, process, executable_path)
