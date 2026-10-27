"""Private native launchers for Windows programs without installed identities."""

from __future__ import annotations

import hashlib
from pathlib import Path, PureWindowsPath

from docking.core.paths import local_path_from_uri_or_path
from docking.log import get_logger
from docking.platform.backends.windows.shortcuts import read_shortcut, write_shortcut
from docking.platform.environment import docking_config_dir

from .constants import FALLBACK_ICON
from .registration import executable_format
from .types import ApplicationInfo, ApplicationLocation, ApplicationOrigin

log = get_logger("windows.executable_registration")


def executable_id(path: Path) -> str:
    # Native Python and an active MSYS2 shell render separators differently.
    # Windows path equality also ignores case; neither changes pin identity.
    canonical = str(PureWindowsPath(path.resolve())).replace("/", "\\").lower()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return f"docking-executable-{digest}"


class WindowsExecutableRegistration:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (
            directory if directory is not None else docking_config_dir() / "launchers"
        )

    def create(self, target: str | Path, *, startup_wm_class: str) -> str | None:
        path = (
            target if isinstance(target, Path) else local_path_from_uri_or_path(target)
        )
        if path is None:
            return None
        try:
            path = path.expanduser().resolve(strict=True)
            # Windows X_OK accepts documents. A direct launcher requires an
            # actual PE executable, never its current document association.
            if path.suffix.lower() != ".exe" or executable_format(path) != "pe":
                return None
            identifier = executable_id(path)
        except (OSError, RuntimeError) as exc:
            log.debug("Could not identify executable launcher: %s", exc)
            return None
        write_shortcut(
            self.directory / f"{identifier}.lnk", executable=path, name=path.stem
        )
        return identifier

    def applications(self) -> tuple[ApplicationInfo, ...]:
        try:
            paths = sorted(self.directory.glob("docking-executable-*.lnk"))
        except OSError as exc:
            log.warning("Could not enumerate executable launchers: %s", exc)
            return ()
        applications = []
        for path in paths:
            try:
                link = read_shortcut(path)
                executable = link.executable
                if not executable.is_absolute() or executable.suffix.lower() != ".exe":
                    continue
                identifier = executable_id(executable)
                if path.stem != identifier or link.arguments:
                    continue
            except (OSError, RuntimeError) as exc:
                log.debug("Could not read executable launcher: %s", exc)
                continue
            basename = executable.stem.lower()
            applications.append(
                ApplicationInfo(
                    desktop_id=identifier,
                    name=executable.stem,
                    declared_icon=FALLBACK_ICON,
                    wm_class=basename,
                    exec_line="",
                    origin=ApplicationOrigin.GENERATED,
                    location=ApplicationLocation.HOST,
                    desktop_file=None,
                    executable_path=executable,
                    aliases=(identifier.lower(), basename),
                    visible=True,
                    has_gio_source=False,
                    working_directory=link.working_directory,
                    launch_target=str(path.resolve()),
                )
            )
        return tuple(applications)

    def directories(self) -> tuple[Path, ...]:
        return (self.directory,)
