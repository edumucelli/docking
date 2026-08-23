# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""XDG desktop-entry parsing, matching, and generated-launcher helpers."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.log import get_logger, with_context
from docking.platform.environment import is_flatpak, xdg_data_home

from .constants import DESKTOP_SUFFIX, FALLBACK_ICON

DEFAULT_XDG_DATA_DIRS = "/usr/local/share:/usr/share"
SNAP_XDG_DATA_DIR = "/var/lib/snapd/desktop"
HOST_XDG_DATA_DIRS = (
    SNAP_XDG_DATA_DIR,
    "/run/host/share",
    "/run/host/usr/local/share",
    "/run/host/usr/share",
    "/run/host/var/lib/flatpak/exports/share",
)
HOST_FILESYSTEM_ROOT = Path("/run/host")
GENERATED_DESKTOP_PREFIX = "docking-generated-"
GENERATED_SOURCE_KEY = "X-Docking-Source-Path"
GENERATED_MARKER_KEY = "X-Docking-Generated"

log = with_context(get_logger(name="desktop_entries"))


@dataclass(frozen=True, slots=True)
class GeneratedDesktopEntry:
    """A user-local desktop file generated for a dropped executable."""

    desktop_id: str
    path: Path
    name: str
    icon_name: str


def normalized_exec_basename(exec_line: str) -> str:
    """Return the lowercase executable basename from a desktop Exec line."""
    if not exec_line:
        return ""
    try:
        argv = shlex.split(exec_line)
    except ValueError:
        return ""
    if not argv:
        return ""
    return Path(argv[0]).name.lower()


def executable_path_from_exec_line(exec_line: str) -> Path | None:
    """Return a canonical direct executable path from a desktop Exec line."""
    if not exec_line:
        return None
    try:
        argv = shlex.split(exec_line)
    except ValueError:
        return None
    if not argv:
        return None
    path = Path(argv[0]).expanduser()
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_file() else None


def wine_executable_aliases(exec_line: str) -> list[str]:
    """Return Wine executable aliases from a desktop Exec line."""
    if not exec_line:
        return []

    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        tokens = re.findall(r"[^\s\"']+|\"[^\"]*\"|'[^']*'", exec_line)

    has_wine = any(Path(token).name.lower() in {"wine", "wine64"} for token in tokens)
    if not has_wine:
        return []

    aliases: list[str] = []
    for match in re.finditer(
        r"""(?ix)
        (?:
            "([^"]+?\.exe)"
            |
            '([^']+?\.exe)'
            |
            ([^\s"']+?\.exe)
        )
        """,
        exec_line,
    ):
        executable = next((group for group in match.groups() if group), "")
        executable = executable.strip().strip("\"'")
        if not executable:
            continue
        basename = re.split(r"[\\/]", executable)[-1].lower()
        if not basename.endswith(".exe"):
            continue
        aliases.append(basename)
        aliases.append(basename[:-4])
    return list(dict.fromkeys(alias for alias in aliases if alias))


def match_aliases(
    desktop_id: str,
    wm_class: str,
    exec_line: str,
) -> list[str]:
    """Return stable runtime aliases from canonical application fields."""
    aliases = [
        wm_class.lower(),
        desktop_id.removesuffix(DESKTOP_SUFFIX).lower(),
    ]
    wine_aliases = wine_executable_aliases(exec_line)
    if wine_aliases:
        aliases.extend(wine_aliases)
    else:
        exec_basename = normalized_exec_basename(exec_line)
        if exec_basename:
            aliases.append(exec_basename)
    return list(dict.fromkeys(alias for alias in aliases if alias))


def desktop_entry_string(key_file: GLib.KeyFile, key: str) -> str:
    try:
        return key_file.get_string("Desktop Entry", key).strip()
    except GLib.Error:
        return ""


def desktop_entry_locale_string(key_file: GLib.KeyFile, key: str) -> str:
    try:
        return key_file.get_locale_string("Desktop Entry", key, None).strip()
    except GLib.Error:
        return desktop_entry_string(key_file, key)


def desktop_entry_bool(key_file: GLib.KeyFile, key: str) -> bool:
    try:
        return bool(key_file.get_boolean("Desktop Entry", key))
    except GLib.Error:
        return False


def load_desktop_key_file(path: Path) -> GLib.KeyFile | None:
    key_file = GLib.KeyFile()
    try:
        key_file.load_from_file(str(path), GLib.KeyFileFlags.NONE)
        return key_file
    except GLib.Error as exc:
        log.bind(action="parse_desktop_file").debug(
            "Failed to parse desktop file %s: %s",
            path,
            exc,
        )
        return None


def desktop_dirs() -> list[Path]:
    """Get application .desktop file directories from XDG_DATA_DIRS."""
    xdg = os.environ.get("XDG_DATA_DIRS", DEFAULT_XDG_DATA_DIRS)
    dirs = []
    for directory in xdg.split(":"):
        path = Path(directory) / "applications"
        if path.is_dir():
            dirs.append(path)
    for directory in HOST_XDG_DATA_DIRS:
        path = Path(directory) / "applications"
        if path.is_dir() and path not in dirs:
            dirs.append(path)
    user_apps = xdg_data_home() / "applications"
    if user_apps.is_dir():
        dirs.insert(0, user_apps)
    if is_flatpak():
        host_user_apps = Path.home() / ".local" / "share" / "applications"
        if host_user_apps.is_dir() and host_user_apps not in dirs:
            dirs.insert(0, host_user_apps)
    return dirs


def user_applications_dir() -> Path:
    """Return the user-local applications directory for desktop entries."""
    return xdg_data_home() / "applications"


def is_host_desktop_file(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        path.relative_to(HOST_FILESYSTEM_ROOT)
        return True
    except ValueError:
        pass

    if not is_flatpak():
        return False
    try:
        path.relative_to(Path.home() / ".local" / "share" / "applications")
        return True
    except ValueError:
        pass

    try:
        path.relative_to(Path(SNAP_XDG_DATA_DIR) / "applications")
        return True
    except ValueError:
        return False


def desktop_file_action_exec(path: Path, action_id: str) -> str:
    key_file = load_desktop_key_file(path)
    if key_file is None:
        return ""
    try:
        return key_file.get_string(f"Desktop Action {action_id}", "Exec").strip()
    except GLib.Error:
        return ""


def desktop_id_from_uri_or_path(target: str) -> str | None:
    """Return a desktop ID from a local .desktop path or file URI."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
    elif parsed.scheme == "":
        path = Path(target).expanduser()
    else:
        return None
    if path.suffix != DESKTOP_SUFFIX:
        return None
    return path.name


def local_path_from_uri_or_path(target: str) -> Path | None:
    """Return a local filesystem path from a file URI or plain local path."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme == "":
        return Path(target).expanduser()
    return None


def appimage_path_needing_executable_permission(target: str | Path) -> Path | None:
    """Return a local AppImage path when it exists but lacks executable permission."""
    if isinstance(target, Path):
        path = target.expanduser()
    else:
        path = local_path_from_uri_or_path(target)
        if path is None:
            return None
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file():
        return None
    if not resolved.name.lower().endswith(".appimage"):
        return None
    if os.access(resolved, os.X_OK):
        return None
    return resolved


def make_user_executable(path: Path) -> bool:
    """Add the user executable bit to a file, preserving existing permissions."""
    try:
        path.chmod(path.stat().st_mode | 0o100)
    except OSError as exc:
        log.bind(action="make_user_executable", path=str(path)).warning(
            "Failed to make file executable: %s",
            exc,
        )
        return False
    return True


def create_desktop_entry_for_executable(
    target: str | Path,
    *,
    startup_wm_class: str = "",
) -> GeneratedDesktopEntry | None:
    """Create or update a generated desktop entry for a launchable local file."""
    path = _local_executable_path(target)
    if path is None:
        return None

    name = _display_name_from_path(path)
    desktop_id = generated_desktop_id_for_path(path)
    desktop_file = user_applications_dir() / desktop_id
    icon_name = _icon_name_for_generated_entry(path)
    content = _generated_desktop_entry_content(
        name=name,
        exec_path=path,
        icon_name=icon_name,
        startup_wm_class=startup_wm_class,
    )

    try:
        _write_desktop_file_atomically(path=desktop_file, content=content)
    except OSError as exc:
        log.bind(
            action="write_generated_desktop_entry", path=str(desktop_file)
        ).warning(
            "Failed to write generated desktop entry: %s",
            exc,
        )
        return None

    _refresh_desktop_database(desktop_file.parent)
    return GeneratedDesktopEntry(
        desktop_id=desktop_id,
        path=desktop_file,
        name=name,
        icon_name=icon_name,
    )


def generated_desktop_id_for_path(path: Path) -> str:
    """Return the stable generated desktop ID for a source executable path."""
    resolved = path.expanduser().resolve()
    slug = _slugify_desktop_name(_display_name_from_path(resolved))
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return f"{GENERATED_DESKTOP_PREFIX}{slug}-{digest}{DESKTOP_SUFFIX}"


def _local_executable_path(target: str | Path) -> Path | None:
    if isinstance(target, Path):
        path = target.expanduser()
    else:
        path = local_path_from_uri_or_path(target)
        if path is None:
            return None
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        log.bind(action="resolve_executable_drop", target=str(target)).debug(
            "Dropped executable path is not resolvable: %s",
            exc,
        )
        return None
    if not resolved.is_file():
        return None
    if not os.access(resolved, os.X_OK):
        if resolved.name.lower().endswith(".appimage"):
            log.bind(path=str(resolved)).info(
                "Dropped AppImage is not executable; refusing to generate launcher"
            )
        return None
    return resolved


def _display_name_from_path(path: Path) -> str:
    stem = path.name
    if stem.lower().endswith(".appimage"):
        stem = stem[: -len(".AppImage")]
    elif path.suffix:
        stem = path.stem
    text = re.sub(r"[_-]+", " ", stem).strip()
    text = re.sub(r"\s+", " ", text)
    return text or path.name


def _slugify_desktop_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "launcher"


def _icon_name_for_generated_entry(path: Path) -> str:
    """Return a colocated executable icon before using a generic fallback."""
    stem = (
        path.name[: -len(".AppImage")]
        if path.name.lower().endswith(".appimage")
        else path.stem
        if path.suffix
        else path.name
    )
    for suffix in (".svg", ".png", ".xpm"):
        candidate = path.with_name(f"{stem}{suffix}")
        if candidate.is_file():
            try:
                return str(candidate.resolve(strict=True))
            except (OSError, RuntimeError):
                continue
    if path.name.lower().endswith(".appimage"):
        return "application-x-appimage"
    return FALLBACK_ICON


def _generated_desktop_entry_content(
    *,
    name: str,
    exec_path: Path,
    icon_name: str,
    startup_wm_class: str = "",
) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={_escape_desktop_value(name)}",
        f"Exec={_quote_exec_arg(str(exec_path))}",
    ]
    if startup_wm_class.strip():
        lines.append(
            f"StartupWMClass={_escape_desktop_value(startup_wm_class.strip())}"
        )
    lines.extend(
        [
            "Terminal=false",
            "Categories=Utility;",
            f"Icon={_escape_desktop_value(icon_name)}",
            f"{GENERATED_MARKER_KEY}=true",
            f"{GENERATED_SOURCE_KEY}={_escape_desktop_value(str(exec_path))}",
            "",
        ]
    )
    return "\n".join(lines)


def _escape_desktop_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def _quote_exec_arg(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")
    escaped = escaped.replace("$", "\\$")
    return f'"{escaped}"'


def _write_desktop_file_atomically(*, path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        tmp_path.chmod(0o644)
        tmp_path.replace(path)
    except Exception:
        with suppress(OSError):
            tmp_path.unlink()
        raise


def _refresh_desktop_database(applications_dir: Path) -> None:
    with suppress(OSError):
        subprocess.Popen(
            ["update-desktop-database", str(applications_dir)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


__all__ = [
    "DEFAULT_XDG_DATA_DIRS",
    "DESKTOP_SUFFIX",
    "FALLBACK_ICON",
    "GENERATED_DESKTOP_PREFIX",
    "GENERATED_MARKER_KEY",
    "GENERATED_SOURCE_KEY",
    "HOST_FILESYSTEM_ROOT",
    "HOST_XDG_DATA_DIRS",
    "SNAP_XDG_DATA_DIR",
    "GeneratedDesktopEntry",
    "_display_name_from_path",
    "_escape_desktop_value",
    "_generated_desktop_entry_content",
    "_icon_name_for_generated_entry",
    "_local_executable_path",
    "_quote_exec_arg",
    "_refresh_desktop_database",
    "_slugify_desktop_name",
    "_write_desktop_file_atomically",
    "appimage_path_needing_executable_permission",
    "create_desktop_entry_for_executable",
    "desktop_dirs",
    "desktop_entry_bool",
    "desktop_entry_locale_string",
    "desktop_entry_string",
    "desktop_file_action_exec",
    "desktop_id_from_uri_or_path",
    "executable_path_from_exec_line",
    "generated_desktop_id_for_path",
    "is_host_desktop_file",
    "load_desktop_key_file",
    "local_path_from_uri_or_path",
    "make_user_executable",
    "match_aliases",
    "normalized_exec_basename",
    "user_applications_dir",
    "wine_executable_aliases",
]
