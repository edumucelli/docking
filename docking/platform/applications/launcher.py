"""Registry-backed launching for selected desktop applications."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.log import get_logger, with_context
from docking.platform import commands
from docking.platform.environment import flatpak, is_flatpak

from . import entries as desktop_entries
from .identity import LaunchProvenanceStore
from .projections import (
    NEW_WINDOW_ACTION_ID,
    new_window_action,
    quicklist_actions,
)
from .registry import ApplicationRegistry
from .types import ApplicationAction, ApplicationInfo, ApplicationLocation

PopenFactory = Callable[..., subprocess.Popen[Any]]

log = with_context(get_logger(name="application_launcher"))


class ApplicationLauncher:
    """Launch registry-selected applications without publishing Gio handles."""

    def __init__(
        self,
        registry: ApplicationRegistry,
        provenance_store: LaunchProvenanceStore,
        *,
        popen: PopenFactory | None = None,
    ) -> None:
        self._registry = registry
        self._provenance_store = provenance_store
        self._popen = subprocess.Popen if popen is None else popen

    def quicklist_actions(
        self,
        desktop_id: str,
    ) -> list[ApplicationAction]:
        """Enumerate source-exclusive dock quicklist actions."""
        application = self._registry.get(desktop_id)
        if application is None:
            return []
        return list(quicklist_actions(application))

    def launch(self, desktop_id: str) -> bool:
        """Launch a selected desktop application through its direct Exec line."""
        application = self._registry.get(desktop_id)
        if application is None:
            return False
        return self._launch_exec_line(
            application=application,
            exec_line=application.exec_line,
            action="launch",
        )

    def launch_action(self, desktop_id: str, action_id: str) -> bool:
        """Launch a source-aware desktop action."""
        application = self._registry.get(desktop_id)
        if application is None:
            return False

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
        ):
            handle = self._registry._gio_handle_for(desktop_id)
            if handle is not None:
                try:
                    handle.launch_action(action_id, None)
                except GLib.Error as exc:
                    log.bind(desktop_id=desktop_id, action="launch_action").warning(
                        "Failed to launch action %s for %s: %s",
                        action_id,
                        desktop_id,
                        exc,
                    )
                    return False
                return True

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

    def launch_new_window(self, desktop_id: str) -> bool:
        """Open a new window when Gio declares that action, else launch normally."""
        application = self._registry.get(desktop_id)
        if application is None:
            return self.launch(desktop_id)

        action = new_window_action(application)
        if action is not None:
            if self.launch_action(desktop_id, NEW_WINDOW_ACTION_ID):
                return True
            if application.location is ApplicationLocation.HOST:
                # Once the host action exists, a missing Exec/flatpak-spawn
                # route does not launch the base application instead.
                return False
        return self.launch(desktop_id)

    def launch_app_uris(self, desktop_id: str, uris: Iterable[str]) -> bool:
        """Open a URI list with one specifically selected application."""
        launchable = list(uris)
        if not launchable:
            return False
        application = self._registry.get(desktop_id)
        if application is None:
            return False

        if application.location is ApplicationLocation.HOST and is_flatpak():
            return self._launch_host_app_uris(
                application=application,
                uris=launchable,
            )

        if not application.has_gio_source:
            return False
        handle = self._registry._gio_handle_for(desktop_id)
        if handle is None:
            return False
        try:
            handle.launch_uris(launchable, None)
        except GLib.Error as exc:
            log.bind(desktop_id=desktop_id, action="launch_app_uris").warning(
                "Failed to open URI list with %s: %s",
                desktop_id,
                exc,
            )
            return False
        return True

    def _launch_host_app_uris(
        self,
        *,
        application: ApplicationInfo,
        uris: list[str],
    ) -> bool:
        """Launch host-owned desktop metadata on the host side of Flatpak."""
        desktop_file = application.desktop_file
        if desktop_file is None:
            return False
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
            return False
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
            return False
        return True

    def launch_listing(self, listing_key: str) -> bool:
        """Launch a transient Gio-backed listing by its opaque registry token."""
        handle = self._registry._gio_handle_for_unidentified(listing_key)
        if handle is None:
            return False
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
    ) -> bool:
        if not exec_line:
            return False
        command = commands.clean_desktop_exec(exec_line)
        if not command:
            return False
        try:
            argv = [
                argument for argument in shlex.split(command, posix=True) if argument
            ]
        except ValueError as exc:
            log.bind(
                desktop_id=application.desktop_id,
                action="parse_exec",
            ).warning(
                "Failed to parse launch command for %s: %s",
                application.desktop_id,
                exc,
            )
            return False
        if not argv:
            return False

        if application.location is ApplicationLocation.HOST:
            host_argv = flatpak.host_command(argv)
            if host_argv is None:
                log.bind(
                    desktop_id=application.desktop_id,
                    action=action,
                ).warning(
                    "Cannot launch host desktop file without flatpak-spawn: %s",
                    application.desktop_file,
                )
                return False
            argv = host_argv

        try:
            process = self._popen(
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
                action=action,
            ).warning(
                "Failed to launch %s: %s",
                application.desktop_id,
                exc,
            )
            return False

        executable_path = (
            application.executable_path
            if exec_line == application.exec_line
            else desktop_entries.executable_path_from_exec_line(exec_line)
        )
        self._provenance_store.record_launch(
            process=process,
            desktop_id=application.desktop_id,
            executable_path=executable_path,
        )
        return True


__all__ = ["ApplicationLauncher"]
