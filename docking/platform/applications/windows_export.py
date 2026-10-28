"""Prepare native launcher files on the metadata worker before publication."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path, PureWindowsPath

from docking.log import get_logger
from docking.platform.backends.windows.drag import file_drag_formats
from docking.platform.backends.windows.shortcuts import write_application_shortcut
from docking.platform.environment import docking_cache_dir

from .types import ApplicationInfo

log = get_logger("windows.application_export")


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

    def prepare(
        self, applications: tuple[ApplicationInfo, ...]
    ) -> tuple[ApplicationInfo, ...]:
        prepared = []
        for app in applications:
            try:
                path = self._file(app)
                formats = file_drag_formats(path) if path is not None else ()
            except (OSError, UnicodeError) as exc:
                log.debug("Could not export an application shortcut: %s", exc)
                path = None
                formats = ()
            prepared.append(replace(app, export_file=path, export_data=formats))
        return tuple(prepared)

    def _file(self, app: ApplicationInfo) -> Path | None:
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
