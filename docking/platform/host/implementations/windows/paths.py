"""Windows filesystem paths, UNC authorities and per-user directory defaults."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path, PureWindowsPath
from string import ascii_letters
from urllib.parse import unquote, urlsplit


def _has_drive(target: str) -> bool:
    return len(target) >= 2 and target[0] in ascii_letters and target[1] == ":"


def is_explicit_path(target: str) -> bool:
    return (
        target.startswith(("/", "~/", "./", "../", "\\", "~\\", ".\\", "..\\"))
        or target.lower().startswith("file:")
        or _has_drive(target)
    )


def _file_url_path(url: str) -> str:
    """Decode Windows file URLs independently of the interpreter's host OS.

    Preserve the established drive/UNC conversion, including legacy drive bars
    and percent-encoded separators, without the deprecated nturl2path module.
    """
    url = url.replace(":", "|")
    if "|" not in url:
        if url.startswith("////"):
            url = url[2:]
        return unquote("\\".join(url.split("/")))
    parts = url.split("|")
    if len(parts) != 2 or not parts[0] or parts[0][-1] not in ascii_letters:
        raise ValueError("Invalid file URL drive")
    path = parts[0][-1].upper() + ":"
    for component in parts[1].split("/"):
        if component:
            path += "\\" + unquote(component)
    if path.endswith(":") and url.endswith("/"):
        path += "\\"
    return path


def local_path_from_uri_or_path(target: str) -> Path | None:
    if not target or "\0" in target:
        return None
    try:
        if _has_drive(target):
            return Path(target).expanduser()
        parsed = urlsplit(target)
        if parsed.scheme == "file":
            if not parsed.path.startswith("/"):
                return None
            authority = parsed.netloc
            if authority.casefold() in {"", "localhost"}:
                authority = ""
            elif any(char in authority for char in "@:\\%"):
                return None
            url_path = f"//{authority}{parsed.path}" if authority else parsed.path
            path = Path(_file_url_path(url_path))
            if not PureWindowsPath(path).is_absolute():
                return None
        elif not parsed.scheme:
            path = Path(target).expanduser()
        else:
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return None if "\0" in str(path) else path


def _windows_folder(variable: str, fallback: str) -> Path:
    return Path(os.environ.get(variable) or Path.home() / "AppData" / fallback)


def xdg_config_home() -> Path:
    return Path(
        os.environ.get("XDG_CONFIG_HOME") or _windows_folder("APPDATA", "Roaming")
    )


def xdg_cache_home() -> Path:
    return Path(
        os.environ.get("XDG_CACHE_HOME") or _windows_folder("LOCALAPPDATA", "Local")
    )


def xdg_data_home() -> Path:
    return Path(
        os.environ.get("XDG_DATA_HOME")
        or _windows_folder("APPDATA", "Roaming") / "Data"
    )


def xdg_state_home() -> Path:
    return Path(
        os.environ.get("XDG_STATE_HOME")
        or _windows_folder("LOCALAPPDATA", "Local") / "State"
    )


def mount_points() -> tuple[Path, ...]:
    from docking.platform.native.windows.ffi import bind

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    read = bind(
        kernel,
        "GetLogicalDriveStringsW",
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
    )
    length = read(0, None)
    if not 0 < length <= 32768:
        raise OSError("Could not determine native volume paths")
    buffer = ctypes.create_unicode_buffer(length + 1)
    actual = read(len(buffer), buffer)
    if not 0 < actual < len(buffer):
        raise OSError("Could not read native volume paths")
    return tuple(Path(value) for value in buffer[:actual].split("\0") if value)
