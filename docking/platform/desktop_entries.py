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

"""Shared XDG desktop-entry discovery and parsing helpers."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib

from docking.log import get_logger, with_context
from docking.platform.environment import is_flatpak, xdg_data_home

DESKTOP_SUFFIX = ".desktop"
FALLBACK_ICON = "application-x-executable"
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

log = with_context(get_logger(name="desktop_entries"))


class DesktopInfo(NamedTuple):
    """Resolved information from a .desktop file."""

    desktop_id: str
    name: str
    icon_name: str
    wm_class: str
    exec_line: str


class DesktopAction(NamedTuple):
    """A .desktop Actions entry, for example ``new-window``."""

    action_id: str
    display_name: str


class DesktopAppListing(NamedTuple):
    """One launchable application visible to menu/applet UIs."""

    desktop_id: str
    name: str
    categories: str
    icon_name: str
    app_info: Gio.DesktopAppInfo | None = None


class ResolvedAppInfo(NamedTuple):
    app_info: Gio.DesktopAppInfo
    desktop_file: Path | None


class ResolvedDesktopLaunch(NamedTuple):
    exec_line: str
    desktop_file: Path | None


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


def desktop_match_aliases(info: DesktopInfo) -> list[str]:
    """Return stable lookup aliases for matching runtime windows to desktop IDs."""
    aliases = [
        info.wm_class.lower(),
        info.desktop_id.removesuffix(DESKTOP_SUFFIX).lower(),
    ]
    exec_basename = normalized_exec_basename(info.exec_line)
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


def desktop_info_from_file(*, desktop_id: str, path: Path) -> DesktopInfo | None:
    key_file = load_desktop_key_file(path)
    if key_file is None:
        return None

    if desktop_entry_string(key_file, "Type") != "Application":
        return None
    if desktop_entry_bool(key_file, "Hidden"):
        return None

    exec_line = desktop_entry_string(key_file, "Exec")
    wm_class = desktop_entry_string(key_file, "StartupWMClass")
    if not wm_class:
        exec_basename = normalized_exec_basename(exec_line)
        wm_class = exec_basename or desktop_id.removesuffix(DESKTOP_SUFFIX)

    return DesktopInfo(
        desktop_id=desktop_id,
        name=desktop_entry_locale_string(key_file, "Name") or desktop_id,
        icon_name=desktop_entry_string(key_file, "Icon") or FALLBACK_ICON,
        wm_class=wm_class,
        exec_line=exec_line,
    )


def desktop_info_from_app_info(
    *, desktop_id: str, app_info: Gio.DesktopAppInfo
) -> DesktopInfo:
    """Build dock metadata from resolved Gio desktop app info."""
    icon = app_info.get_icon()
    icon_name = icon.to_string() if icon else FALLBACK_ICON
    wm_class = wm_class_for_app_info(app_info=app_info, desktop_id=desktop_id)

    return DesktopInfo(
        desktop_id=desktop_id,
        name=app_info.get_display_name() or desktop_id,
        icon_name=icon_name,
        wm_class=wm_class,
        exec_line=app_info.get_commandline() or "",
    )


def wm_class_for_app_info(*, app_info: Gio.DesktopAppInfo, desktop_id: str) -> str:
    """Return explicit StartupWMClass or the existing executable fallback."""
    wm_class = app_info.get_startup_wm_class() or ""
    if wm_class:
        return wm_class
    commandline = app_info.get_commandline() or ""
    exe = commandline.split()[0] if commandline else ""
    return Path(exe).name if exe else desktop_id.removesuffix(DESKTOP_SUFFIX)


def desktop_dirs() -> list[Path]:
    """Get application .desktop file directories from XDG_DATA_DIRS."""
    xdg = os.environ.get("XDG_DATA_DIRS", DEFAULT_XDG_DATA_DIRS)
    dirs = []
    for d in xdg.split(":"):
        p = Path(d) / "applications"
        if p.is_dir():
            dirs.append(p)
    for d in HOST_XDG_DATA_DIRS:
        p = Path(d) / "applications"
        if p.is_dir() and p not in dirs:
            dirs.append(p)
    user_apps = xdg_data_home() / "applications"
    if user_apps.is_dir():
        dirs.insert(0, user_apps)
    if is_flatpak():
        host_user_apps = Path.home() / ".local" / "share" / "applications"
        if host_user_apps.is_dir() and host_user_apps not in dirs:
            dirs.insert(0, host_user_apps)
    return dirs


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


def find_desktop_file(
    desktop_id: str,
    *,
    desktop_dirs_override: list[Path] | None = None,
) -> Path | None:
    for desktop_dir in desktop_dirs_override or desktop_dirs():
        path = desktop_dir / desktop_id
        if path.exists():
            return path
    return None


def resolve_app_info(
    desktop_id: str,
    *,
    action: str,
    desktop_dirs_override: list[Path] | None = None,
    log_failures: bool = True,
) -> ResolvedAppInfo | None:
    resolve_errors: list[str] = []
    try:
        app_info = Gio.DesktopAppInfo.new(desktop_id)
    except (TypeError, GLib.Error) as exc:
        resolve_errors.append(f"desktop app info: {exc}")
        app_info = None
    if app_info is not None:
        return ResolvedAppInfo(
            app_info=app_info,
            desktop_file=find_desktop_file(
                desktop_id,
                desktop_dirs_override=desktop_dirs_override,
            ),
        )

    path = find_desktop_file(
        desktop_id,
        desktop_dirs_override=desktop_dirs_override,
    )
    if path is not None:
        try:
            app_info = Gio.DesktopAppInfo.new_from_filename(str(path))
        except (TypeError, GLib.Error) as exc:
            resolve_errors.append(f"{path}: {exc}")
            app_info = None
        if app_info is not None:
            return ResolvedAppInfo(app_info=app_info, desktop_file=path)

    if log_failures and resolve_errors:
        log.bind(desktop_id=desktop_id, action=action).warning(
            "Failed to resolve desktop app info: %s",
            "; ".join(resolve_errors),
        )
    return None


def resolve_desktop_launch(
    desktop_id: str,
    *,
    action: str,
    desktop_dirs_override: list[Path] | None = None,
) -> ResolvedDesktopLaunch | None:
    resolved = resolve_app_info(
        desktop_id,
        action=action,
        desktop_dirs_override=desktop_dirs_override,
        log_failures=False,
    )
    if resolved is not None:
        return ResolvedDesktopLaunch(
            exec_line=resolved.app_info.get_commandline() or "",
            desktop_file=resolved.desktop_file,
        )

    path = find_desktop_file(
        desktop_id,
        desktop_dirs_override=desktop_dirs_override,
    )
    if path is None:
        return None
    info = desktop_info_from_file(desktop_id=desktop_id, path=path)
    if info is None:
        return None
    return ResolvedDesktopLaunch(exec_line=info.exec_line, desktop_file=path)


def desktop_file_actions(path: Path) -> list[DesktopAction]:
    key_file = load_desktop_key_file(path)
    if key_file is None:
        return []
    try:
        action_ids = key_file.get_string_list("Desktop Entry", "Actions")
    except GLib.Error:
        return []

    result: list[DesktopAction] = []
    for action_id in action_ids:
        group = f"Desktop Action {action_id}"
        try:
            name = key_file.get_locale_string(group, "Name", None).strip()
        except GLib.Error:
            name = ""
        if name:
            result.append(DesktopAction(action_id, name))
    return result


def desktop_file_action_exec(path: Path, action_id: str) -> str:
    key_file = load_desktop_key_file(path)
    if key_file is None:
        return ""
    try:
        return key_file.get_string(f"Desktop Action {action_id}", "Exec").strip()
    except GLib.Error:
        return ""


def desktop_listing_from_file(
    *, desktop_id: str, path: Path
) -> DesktopAppListing | None:
    key_file = load_desktop_key_file(path)
    if key_file is None:
        return None
    if desktop_entry_string(key_file, "Type") != "Application":
        return None
    if desktop_entry_bool(key_file, "Hidden") or desktop_entry_bool(
        key_file,
        "NoDisplay",
    ):
        return None
    return DesktopAppListing(
        desktop_id=desktop_id,
        name=desktop_entry_locale_string(key_file, "Name") or desktop_id,
        categories=desktop_entry_string(key_file, "Categories"),
        icon_name=desktop_entry_string(key_file, "Icon"),
    )


def desktop_listing_from_app_info(
    *, desktop_id: str, app_info: Gio.DesktopAppInfo
) -> DesktopAppListing:
    icon = app_info.get_icon()
    return DesktopAppListing(
        desktop_id=desktop_id,
        name=app_info.get_display_name() or desktop_id or "Unknown",
        categories=app_info.get_categories() or "",
        icon_name=icon.to_string() if icon else "",
        app_info=app_info,
    )


def all_desktop_app_listings() -> list[DesktopAppListing]:
    """Return launchable desktop applications visible to applet UIs."""
    apps: list[DesktopAppListing] = []
    seen: set[str] = set()

    for app_info in Gio.AppInfo.get_all():
        if not isinstance(app_info, Gio.DesktopAppInfo):
            continue
        if app_info.get_is_hidden() or app_info.get_nodisplay():
            continue
        app_id = app_info.get_id()
        if app_id:
            seen.add(app_id)
        apps.append(
            desktop_listing_from_app_info(
                desktop_id=app_id or "",
                app_info=app_info,
            )
        )

    for desktop_dir in desktop_dirs():
        for path in desktop_dir.rglob(f"*{DESKTOP_SUFFIX}"):
            if not path.is_file():
                continue
            desktop_id = path.relative_to(desktop_dir).as_posix()
            if desktop_id in seen:
                continue
            try:
                app_info = Gio.DesktopAppInfo.new_from_filename(str(path))
            except (TypeError, GLib.Error):
                app_info = None
            if app_info is None:
                entry = desktop_listing_from_file(desktop_id=desktop_id, path=path)
            else:
                if app_info.get_is_hidden() or app_info.get_nodisplay():
                    continue
                entry = desktop_listing_from_app_info(
                    desktop_id=desktop_id,
                    app_info=app_info,
                )
            if entry is None:
                continue
            seen.add(desktop_id)
            apps.append(entry)

    return apps


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
