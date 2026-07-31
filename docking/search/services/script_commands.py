"""Discover, validate, and safely execute user-owned script commands."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from docking.applets.runcommand.state import launch_command
from docking.log import get_logger
from docking.platform.environment import flatpak

_KEYWORD_RE = re.compile(r"^[a-z0-9_-]+$")
log = get_logger("search.scripts")


def _user_path_directories() -> tuple[Path, ...]:
    directories: list[Path] = []
    seen: set[Path] = set()
    uid = os.getuid()
    for value in os.environ.get("PATH", "").split(os.pathsep):
        if not value:
            continue
        try:
            directory = Path(value).expanduser().resolve()
            stat = directory.stat()
        except OSError:
            continue
        if directory in seen or not directory.is_dir() or stat.st_uid != uid:
            continue
        seen.add(directory)
        directories.append(directory)
    return tuple(directories)


@dataclass(frozen=True, slots=True)
class ScriptCommand:
    path: Path
    name: str
    keyword: str


def _script_command_from_path(path: Path) -> ScriptCommand | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if (
        not path.is_file()
        or path.is_symlink()
        or stat.st_uid != os.getuid()
        or not os.access(path, os.X_OK)
    ):
        return None
    keyword = path.name.strip().casefold()
    if not _KEYWORD_RE.fullmatch(keyword):
        return None
    return ScriptCommand(
        path=path,
        name=path.name.replace("-", " ").title(),
        keyword=keyword,
    )


class ScriptCommandCatalog:
    """Small on-demand catalog; directories are scanned only for ``cmd`` queries."""

    def __init__(self) -> None:
        self._directories = _user_path_directories()

    @property
    def directories(self) -> tuple[Path, ...]:
        return self._directories

    def snapshot(self) -> tuple[ScriptCommand, ...]:
        commands: dict[str, ScriptCommand] = {}
        for directory in self._directories:
            if not directory.is_dir():
                continue
            try:
                children = tuple(
                    sorted(directory.iterdir(), key=lambda path: path.name.casefold())
                )
            except OSError:
                continue
            for path in children:
                command = _script_command_from_path(path)
                if command is not None:
                    if command.keyword in commands:
                        log.warning(
                            "Ignoring duplicate script keyword %s from %s",
                            command.keyword,
                            command.path,
                        )
                        continue
                    commands[command.keyword] = command
        return tuple(
            sorted(
                commands.values(),
                key=lambda command: (command.name.casefold(), command.keyword),
            )
        )


def execute_script(
    *,
    command: ScriptCommand,
    arguments: tuple[str, ...],
    run_in_terminal: bool,
) -> bool:
    refreshed = _script_command_from_path(command.path)
    if refreshed is None or refreshed.keyword != command.keyword:
        return False
    argv = [str(refreshed.path), *arguments]
    if run_in_terminal:
        return launch_command(
            command=shlex.join(argv),
            run_in_terminal=True,
        )
    host_argv = flatpak.host_command(argv) or argv
    try:
        subprocess.Popen(
            host_argv,
            shell=False,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return True


__all__ = [
    "ScriptCommand",
    "ScriptCommandCatalog",
    "execute_script",
]
