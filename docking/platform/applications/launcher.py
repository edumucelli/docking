"""Registry-backed launching for selected desktop applications."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Iterable
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.log import get_logger, with_context
from docking.platform import commands
from docking.platform.environment import flatpak

from . import entries as desktop_entries
from .identity import LaunchProvenanceStore
from .projections import (
    NEW_WINDOW_ACTION_ID,
    new_window_action,
)
from .projections import (
    quicklist_actions as project_quicklist_actions,
)
from .registry import ApplicationRegistry
from .types import ApplicationAction, ApplicationInfo, ApplicationLocation

PopenFactory = Callable[..., subprocess.Popen[Any]]
GioLaunch = Callable[[object], object]
GioActionLaunch = Callable[[object, str], object]
GioUrisLaunch = Callable[[object, list[str]], object]

log = with_context(get_logger(name="application_launcher"))


def _launch_gio_application(handle: object) -> object:
    return handle.launch([], None)


def _launch_gio_action(handle: object, action_id: str) -> object:
    return handle.launch_action(action_id, None)


def _launch_gio_uris(handle: object, uris: list[str]) -> object:
    return handle.launch_uris(uris, None)


class ApplicationLauncher:
    """Launch registry-selected applications without publishing Gio handles."""

    def __init__(
        self,
        registry: ApplicationRegistry,
        provenance_store: LaunchProvenanceStore,
        *,
        popen: PopenFactory | None = None,
        gio_launch: GioLaunch | None = None,
        gio_launch_action: GioActionLaunch | None = None,
        gio_launch_uris: GioUrisLaunch | None = None,
    ) -> None:
        self._registry = registry
        self._provenance_store = provenance_store
        self._popen = popen
        self._gio_launch = gio_launch or _launch_gio_application
        self._gio_launch_action = gio_launch_action or _launch_gio_action
        self._gio_launch_uris = gio_launch_uris or _launch_gio_uris

    @property
    def registry(self) -> ApplicationRegistry:
        """Return the borrowed application registry."""
        return self._registry

    @property
    def provenance_store(self) -> LaunchProvenanceStore:
        """Return the launch store shared with process identity matching."""
        return self._provenance_store

    def get_actions(self, desktop_id: str) -> list[desktop_entries.DesktopAction]:
        """Return source-exclusive dock quicklist actions."""
        return self.quicklist_actions(desktop_id)

    def quicklist_actions(
        self,
        desktop_id: str,
    ) -> list[desktop_entries.DesktopAction]:
        """Enumerate source-exclusive dock quicklist actions."""
        application = self._registry.resolve(desktop_id, log_failures=False)
        if application is None:
            return []
        return [
            desktop_entries.DesktopAction(action.action_id, action.name)
            for action in project_quicklist_actions(application)
        ]

    def launch(self, desktop_id: str) -> bool:
        """Launch a selected desktop application through its direct Exec line."""
        application = self._registry.resolve(desktop_id)
        if application is None:
            return False
        return self._launch_exec_line(
            application=application,
            exec_line=application.exec_line,
            action="launch",
        )

    def launch_action(self, desktop_id: str, action_id: str) -> bool:
        """Launch a source-aware desktop action."""
        application = self._registry.resolve(desktop_id, log_failures=False)
        if application is None:
            return False

        action = _action_for(application, action_id)
        if (
            application.location is ApplicationLocation.SANDBOX
            and application.has_gio_source
        ):
            handle = self._registry._gio_handle_for(desktop_id)
            if handle is not None:
                return self._run_gio_action(
                    handle=handle,
                    desktop_id=desktop_id,
                    action_id=action_id,
                )

        exec_line = _file_action_exec_line(
            application=application,
            action=action,
            action_id=action_id,
        )
        return self._launch_exec_line(
            application=application,
            exec_line=exec_line,
            action="launch_action",
        )

    def launch_new_window(self, desktop_id: str) -> bool:
        """Open a new window when Gio declares that action, else launch normally."""
        application = self._registry.resolve(desktop_id, log_failures=False)
        if application is None:
            return self.launch(desktop_id)

        action = new_window_action(application)
        if action is not None:
            if self.launch_action(desktop_id, NEW_WINDOW_ACTION_ID):
                return True
            if application.location is ApplicationLocation.HOST:
                # Preserve the legacy host-action route: once the action exists,
                # a missing host Exec/flatpak-spawn does not launch the base app.
                return False
        return self.launch(desktop_id)

    def launch_app_uris(self, desktop_id: str, uris: Iterable[str]) -> bool:
        """Open a URI list with one specifically selected Gio application."""
        launchable = list(uris)
        if not launchable:
            return False
        application = self._registry.resolve(desktop_id, log_failures=False)
        if application is None or not application.has_gio_source:
            return False
        handle = self._registry._gio_handle_for(desktop_id)
        if handle is None:
            return False
        try:
            self._gio_launch_uris(handle, launchable)
        except GLib.Error as exc:
            log.bind(desktop_id=desktop_id, action="launch_app_uris").warning(
                "Failed to open URI list with %s: %s",
                desktop_id,
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
            self._gio_launch(handle)
        except GLib.Error as exc:
            log.bind(listing_key=listing_key, action="launch_listing").warning(
                "Failed to launch application listing %s: %s",
                listing_key,
                exc,
            )
            return False
        return True

    def launch_unidentified(self, listing_key: str) -> bool:
        """Compatibility alias for launching an opaque listing token."""
        return self.launch_listing(listing_key)

    def _run_gio_action(
        self,
        *,
        handle: object,
        desktop_id: str,
        action_id: str,
    ) -> bool:
        try:
            self._gio_launch_action(handle, action_id)
        except GLib.Error as exc:
            log.bind(desktop_id=desktop_id, action="launch_action").warning(
                "Failed to launch action %s for %s: %s",
                action_id,
                desktop_id,
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
            process = (self._popen or subprocess.Popen)(
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


def _action_for(
    application: ApplicationInfo,
    action_id: str,
) -> ApplicationAction | None:
    return next(
        (action for action in application.actions if action.action_id == action_id),
        None,
    )


def _file_action_exec_line(
    *,
    application: ApplicationInfo,
    action: ApplicationAction | None,
    action_id: str,
) -> str:
    if action is not None and action.file_exec_line:
        return action.file_exec_line
    if application.desktop_file is None:
        return ""
    return desktop_entries.desktop_file_action_exec(
        application.desktop_file,
        action_id,
    )


__all__ = ["ApplicationLauncher"]
