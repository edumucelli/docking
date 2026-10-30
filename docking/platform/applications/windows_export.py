"""Prepare native launcher files on the metadata worker before publication."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import tempfile
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path, PureWindowsPath

from docking.log import get_logger
from docking.platform.backends.windows.drag import file_drag_formats
from docking.platform.backends.windows.shortcuts import write_application_shortcut
from docking.platform.environment import docking_cache_dir

from .types import ApplicationInfo

log = get_logger("windows.application_export")
_PREPARED_LIMIT = 256


def _signature(path: Path) -> tuple[int, int, int, int]:
    state = path.stat()
    return state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns


def _filename(name: str) -> str:
    cleaned = "".join(
        "_" if ord(c) < 32 or c in '<>:"/\\|?*' else c for c in name
    ).strip(" .")
    cleaned = cleaned.encode("utf-16-le")[:160].decode("utf-16-le", errors="ignore")
    cleaned = cleaned.rstrip(" .") or "Application"
    base = cleaned.split(".", 1)[0].upper()
    if base in {"CON", "PRN", "AUX", "NUL"} or (
        len(base) == 4 and base[:3] in {"COM", "LPT"} and base[3] in "123456789¹²³"
    ):
        cleaned = "_" + cleaned
    return cleaned + ".lnk"


class WindowsApplicationExport:
    """Persistent cache files stay valid through Explorer's asynchronous copy."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = (
            directory
            if directory is not None
            else docking_cache_dir() / "application-exports"
        )
        self._prepared: OrderedDict[
            ApplicationInfo, tuple[tuple[int, int, int, int], ApplicationInfo]
        ] = OrderedDict()

    def prepare(
        self,
        applications: tuple[ApplicationInfo, ...],
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> tuple[ApplicationInfo, ...]:
        prepared = []
        for app in applications:
            if cancelled():
                return applications
            try:
                cached = self._prepared.get(app)
                if cached is not None:
                    signature, previous = cached
                    assert previous.export_file is not None
                    try:
                        current = _signature(previous.export_file)
                    except FileNotFoundError:
                        current = None
                    if signature == current:
                        self._prepared.move_to_end(app)
                        prepared.append(previous)
                        continue
                    self._prepared.pop(app)
                path = self._file(app)
                if cancelled():
                    return applications
                formats = file_drag_formats(path) if path is not None else ()
                if cancelled():
                    return applications
                result = replace(app, export_file=path, export_data=formats)
                if path is not None and formats:
                    self._prepared[app] = _signature(path), result
                    while len(self._prepared) > _PREPARED_LIMIT:
                        self._prepared.popitem(last=False)
            except (OSError, UnicodeError) as exc:
                log.debug(
                    "Could not export an application shortcut (error %s)",
                    exc.errno if isinstance(exc, OSError) else None,
                )
                result = replace(app, export_file=None, export_data=())
            prepared.append(result)
        return tuple(prepared)

    def prepare_documents(
        self,
        applications: tuple[ApplicationInfo, ...],
        sources: Mapping[str, tuple[tuple[Path, os.stat_result], ...]],
        *,
        cancelled: Callable[[], bool],
    ) -> tuple[ApplicationInfo, ...]:
        prepared = []
        for app in applications:
            if cancelled():
                return applications
            candidates = sources.get(app.desktop_id, ())
            target = None
            try:
                for source, identity in candidates:
                    snapshot = self._snapshot(app, source, identity, cancelled)
                    if target is not None and target != snapshot:
                        target = None  # Same native ID, different selected options.
                        break
                    target = snapshot
            except (OSError, UnicodeError) as exc:
                log.debug(
                    "Could not snapshot an application shortcut (error %s)",
                    exc.errno if isinstance(exc, OSError) else None,
                )
                target = None
            prepared.append(replace(app, document_target=str(target) if target else ""))
        return tuple(prepared)

    def _snapshot(
        self,
        app: ApplicationInfo,
        source: Path,
        identity: os.stat_result,
        cancelled: Callable[[], bool],
    ) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(
            (app.desktop_id + "\0" + app.name + "\0").encode("utf-8")
        )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.directory, prefix=".snapshot-", suffix=".lnk", delete=False
            ) as stream:
                temporary = Path(stream.name)
                with source.open("rb") as original:
                    remaining = identity.st_size
                    while data := original.read(min(65536, remaining + 1)):
                        if cancelled():
                            raise OSError(
                                errno.ECANCELED, "Shortcut snapshot cancelled"
                            )
                        remaining -= len(data)
                        if remaining < 0:
                            raise OSError(
                                errno.EAGAIN, "Shortcut grew during discovery"
                            )
                        digest.update(data)
                        stream.write(data)
                current = source.stat()
                if (
                    identity.st_dev,
                    identity.st_ino,
                    identity.st_size,
                    identity.st_mtime_ns,
                ) != (
                    current.st_dev,
                    current.st_ino,
                    current.st_size,
                    current.st_mtime_ns,
                ):
                    raise OSError(
                        errno.EAGAIN, "Shortcut changed during catalog discovery"
                    )
            if cancelled():
                raise OSError(errno.ECANCELED, "Shortcut snapshot cancelled")
            path = (
                self.directory.resolve() / digest.hexdigest()[:32] / _filename(app.name)
            )
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary.replace(path)
            return path
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _file(self, app: ApplicationInfo) -> Path | None:
        if app.document_target:
            path = Path(app.document_target)
            return path if path.is_file() else None
        target = app.launch_target
        source = PureWindowsPath(target)
        is_link = source.is_absolute() and source.suffix.lower() == ".lnk"
        if not is_link and not target.startswith("shell:AppsFolder\\"):
            return None
        canonical = str(source).replace("/", "\\").lower() if is_link else target
        digest = hashlib.sha256(
            (app.desktop_id + "\0" + app.name + "\0" + canonical).encode("utf-8")
        ).hexdigest()[:32]
        path = self.directory.resolve() / digest / _filename(app.name)
        if path.is_file():
            return path
        if is_link:
            # The source is an immutable registration generation. Preserve all
            # native launch options; never flatten it into an executable command.
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".export-", suffix=".lnk", delete=False
            ) as stream:
                temporary = Path(stream.name)
            try:
                shutil.copyfile(Path(target), temporary)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            write_application_shortcut(
                path,
                target=target,
                application_id=app.desktop_id,
                name=app.name,
            )
        return path
