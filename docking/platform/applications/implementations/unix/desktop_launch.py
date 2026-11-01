"""Unix desktop Exec, terminal, working-directory and Flatpak launch mechanics."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from shlex import join as shell_join
from typing import Any

from docking.log import get_logger, with_context
from docking.platform import commands
from docking.platform.environment import flatpak, is_flatpak

from ...backend import LaunchedProcess, LaunchResult
from ...types import ApplicationInfo, ApplicationLocation
from . import entries as desktop_entries

PopenFactory = Callable[..., subprocess.Popen[Any]]
log = with_context(get_logger(name="application_launcher"))


class DesktopExecLauncher:
    def __init__(self, *, popen: PopenFactory | None = None) -> None:
        self._popen = subprocess.Popen if popen is None else popen

    def _launch_exec_line(
        self,
        *,
        application: ApplicationInfo,
        exec_line: str,
        action: str,
    ) -> LaunchResult:
        if not exec_line:
            return LaunchResult(False)
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
            return LaunchResult(False)
        if not argv:
            return LaunchResult(False)

        if application.terminal:
            if application.location is ApplicationLocation.SANDBOX and is_flatpak():
                terminal_argv = commands.build_terminal_argv(
                    command=shell_join(argv),
                    resolver=commands.resolve_terminal_executable,
                )
            else:
                terminal_argv = commands.build_terminal_argv(command=shell_join(argv))
            if terminal_argv is None:
                return LaunchResult(False)
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
                return LaunchResult(False)
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
            return LaunchResult(False)

        executable_path = (
            application.executable_path
            if exec_line == application.exec_line
            else desktop_entries.executable_path_from_exec_line(exec_line)
        )
        return LaunchResult(True, LaunchedProcess(process, executable_path))
