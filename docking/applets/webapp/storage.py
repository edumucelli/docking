"""Dedicated website directories and download names shared by web-app hosts."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.core.paths import ensure_dir
from docking.i18n import _
from docking.platform.environment.xdg import docking_cache_dir, docking_data_dir


def _session_directories(session_name: str = "whatsapp") -> tuple[Path, Path]:
    data_dir = ensure_dir(docking_data_dir() / session_name)
    cache_dir = ensure_dir(docking_cache_dir() / session_name)
    for directory in (data_dir, cache_dir):
        with suppress(OSError):
            directory.chmod(0o700)
    return data_dir, cache_dir


def _unique_download_path(suggested_filename: str) -> Path:
    raw_name = Path(suggested_filename or "download").name
    filename = raw_name if raw_name not in {"", ".", ".."} else "download"
    special_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    downloads = ensure_dir(
        Path(special_dir) if special_dir else Path.home() / "Downloads"
    )
    candidate = downloads / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem or "download"
    suffix = candidate.suffix
    for index in range(1, 10_000):
        alternative = downloads / f"{stem} ({index}){suffix}"
        if not alternative.exists():
            return alternative
    return downloads / f"{stem}-{GLib.get_monotonic_time()}{suffix}"


def _error_message(error: object) -> str:
    message = str(error).strip().splitlines()[0] if str(error).strip() else ""
    return message[:160] or _("unknown browser error")
