"""Private native launchers for Windows programs without installed identities."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path, PureWindowsPath

from docking.core.paths import local_path_from_uri_or_path
from docking.log import get_logger
from docking.platform.backends.windows.appresolver import shortcut_application_id
from docking.platform.backends.windows.shortcuts import read_shortcut, write_shortcut
from docking.platform.environment import docking_config_dir

from ..constants import FALLBACK_ICON
from ..registration import executable_format
from ..types import ApplicationInfo, ApplicationLocation, ApplicationOrigin

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
        except (OSError, RuntimeError) as exc:
            log.debug("Could not identify executable launcher: %s", exc)
            return None
        if path.suffix.lower() == ".lnk":
            return self._import_shortcut(path)
        try:
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

    def _import_shortcut(self, source: Path) -> str | None:
        before = source.stat()
        link = read_shortcut(source)
        target = link.executable
        if target is not None:
            if target.is_dir():
                return None
            if target.is_file() and executable_format(target) != "pe":
                return None
            if not target.is_file() and target.suffix.lower() not in {
                ".exe",
                ".com",
                ".scr",
            }:
                return None
        try:
            identifier = shortcut_application_id(source)
        except OSError:
            identifier = None
        if identifier is None:
            if target is None:
                raise OSError("Shortcut has no identifiable application")
            canonical = str(PureWindowsPath(source)).replace("/", "\\").lower()
            identifier = (
                "docking-shortcut-"
                + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
            )
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]
        prefix = f"docking-shortcut-{digest}"
        manifest = self.directory / f"{prefix}.json"
        previous = self._import_manifest(manifest)
        old_file = previous[2] if previous is not None else None
        self.directory.mkdir(parents=True, exist_ok=True)
        generation = self.directory / f"{prefix}-{uuid.uuid4().hex}.lnk"
        temporary = None
        try:
            # Preserve the native binary, including arguments, working directory,
            # icon, shell target, elevation and other Windows launch options.
            shutil.copyfile(source, generation)
            after = source.stat()
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                # Identity/validation and the persisted binary must describe one
                # source instance. Retain the last registration when Explorer or
                # an installer replaces the selected link during this operation.
                raise OSError(errno.EAGAIN, "Shortcut changed during import")
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=f".{prefix}-",
                suffix=".json",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(
                    {
                        "version": 1,
                        "id": identifier,
                        "name": source.stem,
                        "file": generation.name,
                    },
                    stream,
                    ensure_ascii=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(manifest)
        except OSError:
            generation.unlink(missing_ok=True)
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        # Keep the immediately preceding binary for readers still holding the
        # previous immutable registry generation; remove older owned copies.
        retained = {generation.resolve()}
        if old_file is not None:
            retained.add(old_file.resolve())
        for obsolete in self.directory.glob(f"{prefix}-*.lnk"):
            if obsolete.resolve() not in retained:
                try:
                    obsolete.unlink()
                except OSError as exc:
                    log.debug("Could not remove an old shortcut generation: %s", exc)
        return identifier

    def _import_manifest(self, path: Path) -> tuple[str, str, Path] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            log.debug("Could not read shortcut registration: %s", exc)
            return None
        if not isinstance(data, dict) or data.get("version") != 1:
            return None
        identifier, name, filename = data.get("id"), data.get("name"), data.get("file")
        if (
            not isinstance(identifier, str)
            or not isinstance(name, str)
            or not isinstance(filename, str)
            or not all(
                value and "\0" not in value for value in (identifier, name, filename)
            )
        ):
            return None
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]
        prefix = f"docking-shortcut-{digest}"
        if (
            path.stem != prefix
            or "/" in filename
            or "\\" in filename
            or not filename.startswith(prefix + "-")
            or not filename.endswith(".lnk")
        ):
            return None
        return identifier, name, self.directory / filename

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
                if (
                    executable is None
                    or not executable.is_absolute()
                    or executable.suffix.lower() != ".exe"
                ):
                    continue
                identifier = executable_id(executable)
                if path.stem != identifier or link.arguments:
                    continue
            except (OSError, RuntimeError) as exc:
                log.debug("Could not read executable launcher: %s", exc)
                continue
            applications.append(
                self._application(
                    identifier,
                    executable.stem,
                    executable,
                    link.working_directory,
                    path,
                )
            )
        for manifest in sorted(self.directory.glob("docking-shortcut-*.json")):
            record = self._import_manifest(manifest)
            if record is None:
                continue
            identifier, name, path = record
            try:
                link = read_shortcut(path)
            except OSError as exc:
                log.debug("Could not read imported shortcut: %s", exc)
                continue
            applications.append(
                self._application(
                    identifier, name, link.executable, link.working_directory, path
                )
            )
        return tuple(applications)

    @staticmethod
    def _application(
        identifier: str,
        name: str,
        executable: Path | None,
        working_directory: str,
        path: Path,
    ) -> ApplicationInfo:
        basename = executable.stem.lower() if executable is not None else ""
        return ApplicationInfo(
            desktop_id=identifier,
            name=name,
            declared_icon=FALLBACK_ICON,
            wm_class=basename,
            exec_line="",
            origin=ApplicationOrigin.GENERATED,
            location=ApplicationLocation.HOST,
            desktop_file=None,
            executable_path=executable,
            aliases=tuple(v for v in (identifier.lower(), basename) if v),
            visible=True,
            has_gio_source=False,
            working_directory=working_directory,
            launch_target=str(path.resolve()),
        )

    def directories(self) -> tuple[Path, ...]:
        return (self.directory,)
