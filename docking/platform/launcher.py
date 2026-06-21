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

"""Icon loading, file-target metadata, and desktop launch execution.

Why this module exists

The dock cannot work directly with desktop IDs and file URIs as opaque strings.
To draw and launch something meaningful it needs to answer questions such as:

- what user-visible name should appear,
- which icon should be loaded,
- what WM_CLASS should be matched against running windows,
- what command or URI should actually be launched,
- how should a file/folder target be represented in the dock.

Those are not UI concerns and they are not model concerns. They are platform
integration concerns, which is why they live here.

Two major jobs

This module has two distinct responsibilities:

1. Resolve runtime target metadata
   - desktop entry metadata from ``platform.desktop_entries``
   - file/folder target -> FileTargetInfo

2. Execute launch/open actions
   - start applications from desktop files
   - open files/folders with the desktop environment

The rest of the dock should not need to know how Gio, icon themes, file
metadata, or command placeholders work. XDG desktop-entry parsing and discovery
live in ``docking.platform.desktop_entries`` so applets, DnD, recent documents,
and launcher execution share the same behavior.

Desktop entry resolution

Application dock entries are identified by desktop IDs such as:

    firefox.desktop
    org.gnome.Nautilus.desktop

Those IDs are packaging/runtime artifacts, not rich metadata by themselves.
The dock needs to expand them into:

- display name
- icon name
- startup WM_CLASS
- exec line

That is the purpose of ``desktop_entries.DesktopInfo``.

Resolution flow:

    desktop_id
      |
      +--> Gio.DesktopAppInfo.new(...)
      |
      +--> fallback search in XDG desktop dirs if necessary
      |
      +--> extract name/icon/wm_class/exec

Why WM_CLASS fallback exists

Not every desktop file gives a clean startup WM_CLASS. When it is missing, the
dock still needs a plausible identity string for window matching. So this module
derives a fallback from the executable or desktop filename.

That fallback is not perfect, but it is far better than leaving the dock with no
runtime identity bridge at all.

Icon loading model

The dock consumes icons from several possible sources:

- named theme icon
- Gio.Icon
- absolute file path
- generic fallback icon

So icon loading works in layers:

    requested icon
      |
      +--> load from Gio.Icon if available
      |
      +--> otherwise load named icon from theme
      |
      +--> otherwise load absolute path
      |
      +--> otherwise fallback icon

Icons are cached by `(icon identity, size)` because the dock requests the same
art assets repeatedly during rendering and model refresh.

File and folder targets

The dock also supports file/folder entries. Those are different from apps:

- they are opened, not launched as desktop apps,
- their icon/name come from filesystem metadata,
- directories and regular files have different default icon semantics.

So this module normalizes file targets into `FileTargetInfo` with:

- resolved display name
- icon or theme fallback
- whether the target is a directory

That keeps file/folder handling aligned with the rest of the dock item model.

Why this module is deliberately defensive

Desktop metadata is not always clean:

- invalid desktop files exist,
- icons may be missing,
- files may disappear,
- Gio may fail to resolve specific targets.

The dock should degrade gracefully:

    no desktop info -> item may not resolve
    no icon         -> generic fallback icon
    bad file target -> no dock entry metadata

This module treats those failures as data-quality issues, not fatal UI errors.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from docking.core.config import MiddleClickAction
from docking.log import get_logger, with_context
from docking.platform import desktop_entries
from docking.platform.environment import flatpak, is_flatpak

DESKTOP_SUFFIX = ".desktop"
FALLBACK_ICON = "application-x-executable"
DEFAULT_XDG_DATA_DIRS = "/usr/local/share:/usr/share"
GNOME_APP_PREFIX = "org.gnome."
FILE_ICON_CACHE_MAX_ENTRIES = 256
SNAP_XDG_DATA_DIR = "/var/lib/snapd/desktop"
HOST_XDG_DATA_DIRS = (
    SNAP_XDG_DATA_DIR,
    "/run/host/share",
    "/run/host/usr/local/share",
    "/run/host/usr/share",
    "/run/host/var/lib/flatpak/exports/share",
)
HOST_PIXMAP_DIRS = tuple(f"{data_dir}/pixmaps" for data_dir in HOST_XDG_DATA_DIRS)
HOST_FILESYSTEM_ROOT = Path("/run/host")
ICON_FILE_EXTENSIONS = (".png", ".svg", ".xpm")

log = with_context(get_logger(name="launcher"))


class FileTargetInfo(NamedTuple):
    """Resolved file/folder metadata for dock entries."""

    target: str
    name: str
    icon_name: str
    icon: GdkPixbuf.Pixbuf | None
    is_dir: bool


def _host_icon_file_candidates(icon_name: str) -> list[Path]:
    icon_path = Path(icon_name)
    if not icon_path.is_absolute():
        return []

    candidates = [icon_path]
    host_root = str(HOST_FILESYSTEM_ROOT)
    if not str(icon_path).startswith(f"{host_root}{os.sep}"):
        candidates.append(HOST_FILESYSTEM_ROOT / str(icon_path).lstrip(os.sep))
    return candidates


def _create_icon_theme() -> Gtk.IconTheme:
    theme = Gtk.IconTheme.get_default()
    if theme is None:
        theme = Gtk.IconTheme()
        theme.set_custom_theme("hicolor")
    existing = set(theme.get_search_path())
    for pixmaps_dir in HOST_PIXMAP_DIRS:
        if Path(pixmaps_dir).is_dir() and pixmaps_dir not in existing:
            theme.append_search_path(pixmaps_dir)
            existing.add(pixmaps_dir)
    return theme


def _theme_icon_candidates(icon_name: str) -> list[str]:
    candidates = [icon_name]
    if Path(icon_name).parent == Path() and Path(icon_name).suffix.lower() in (
        ".png",
        ".svg",
        ".xpm",
    ):
        candidates.append(Path(icon_name).stem)
    if icon_name.startswith(GNOME_APP_PREFIX):
        name = icon_name.removeprefix(GNOME_APP_PREFIX)
        legacy = f"gnome-{name.replace('.', '-').lower()}"
        candidates.append(legacy)
        candidates.append(name.replace(".", "-").lower())
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


class Launcher:
    """Resolves .desktop files via XDG_DATA_DIRS and loads icons."""

    def __init__(self) -> None:
        self._desktop_dirs = desktop_entries.desktop_dirs()
        self._icon_cache: dict[tuple[str, int], GdkPixbuf.Pixbuf | None] = {}
        self._file_icon_cache: dict[
            tuple[str, int, int, int], GdkPixbuf.Pixbuf | None
        ] = {}
        self._wm_class_index: dict[str, desktop_entries.DesktopInfo] | None = None

    def resolve(
        self, desktop_id: str, *, log_failures: bool = True
    ) -> desktop_entries.DesktopInfo | None:
        """Resolve a desktop ID (e.g. 'firefox.desktop') to full info."""
        resolve_errors: list[str] = []
        app_info = self._desktop_app_info_for_id(
            desktop_id=desktop_id,
            resolve_errors=resolve_errors,
        )
        if app_info is None:
            info = self._resolve_desktop_file(desktop_id=desktop_id)
        else:
            info = desktop_entries.desktop_info_from_app_info(
                desktop_id=desktop_id,
                app_info=app_info,
            )
        if info is None:
            if log_failures and resolve_errors:
                log.bind(desktop_id=desktop_id, action="resolve").warning(
                    "Failed to resolve desktop file: %s",
                    "; ".join(resolve_errors),
                )
            return None

        self._cache_resolved_aliases(info=info)
        return info

    def _desktop_app_info_for_id(
        self, *, desktop_id: str, resolve_errors: list[str]
    ) -> Gio.DesktopAppInfo | None:
        """Resolve desktop app info using Gio, then XDG desktop dirs."""
        try:
            app_info = Gio.DesktopAppInfo.new(desktop_id)
        except (TypeError, GLib.Error) as exc:
            resolve_errors.append(f"desktop app info: {exc}")
            app_info = None
        # Use "is not None" deliberately. Gio objects may define truthiness in
        # surprising ways, and the old resolver only fell back when Gio returned
        # None. Falling back on falsey objects can pick different metadata from
        # XDG files and change names/icons/WM_CLASS.
        if app_info is not None:
            return app_info
        return self._desktop_app_info_from_xdg_dirs(
            desktop_id=desktop_id,
            resolve_errors=resolve_errors,
        )

    def _desktop_app_info_from_xdg_dirs(
        self, *, desktop_id: str, resolve_errors: list[str]
    ) -> Gio.DesktopAppInfo | None:
        """Fallback desktop lookup by filename under XDG application dirs."""
        for directory in self._desktop_dirs:
            path = directory / desktop_id
            if not path.exists():
                continue
            try:
                return Gio.DesktopAppInfo.new_from_filename(str(path))
            except (TypeError, GLib.Error) as exc:
                resolve_errors.append(f"{path}: {exc}")
        return None

    def _resolve_desktop_file(
        self, *, desktop_id: str
    ) -> desktop_entries.DesktopInfo | None:
        for desktop_dir in self._desktop_dirs:
            path = desktop_dir / desktop_id
            if path.is_file():
                return desktop_entries.desktop_info_from_file(
                    desktop_id=desktop_id,
                    path=path,
                )
        return None

    def _cache_resolved_aliases(self, *, info: desktop_entries.DesktopInfo) -> None:
        if self._wm_class_index is None:
            return
        # The install-wide index is lazy. Resolving one desktop file before
        # the index exists should not force a full scan, but once the index
        # has been built this keeps later direct resolves visible to
        # resolve_by_wm_class.
        for alias in desktop_entries.desktop_match_aliases(info):
            self._wm_class_index.setdefault(alias, info)

    def resolve_by_wm_class(self, wm_class: str) -> desktop_entries.DesktopInfo | None:
        """Resolve an installed desktop file by runtime WM_CLASS or executable alias."""
        lookup = wm_class.lower().strip()
        if not lookup:
            return None
        if self._wm_class_index is None:
            self._build_wm_class_index()
        if self._wm_class_index is None:
            return None
        return self._wm_class_index.get(lookup)

    def refresh_desktop_entries(self) -> None:
        """Reload desktop-entry search paths and invalidate runtime alias cache."""
        self._desktop_dirs = desktop_entries.desktop_dirs()
        self._wm_class_index = None

    def load_icon(self, icon_name: str, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load an icon by name at the given size, with caching."""
        key = (icon_name, size)
        if key in self._icon_cache:
            return self._icon_cache[key]

        pixbuf = self._try_load_icon(icon_name=icon_name, size=size)
        self._icon_cache[key] = pixbuf
        return pixbuf

    def load_desktop_icon(
        self, info: desktop_entries.DesktopInfo, size: int
    ) -> GdkPixbuf.Pixbuf | None:
        """Load an application icon with desktop-entry fallbacks."""
        key = (f"desktop:{info.desktop_id}:{info.icon_name}:{info.exec_line}", size)
        if key in self._icon_cache:
            return self._icon_cache[key]

        candidates = [
            info.icon_name,
            desktop_entries.normalized_exec_basename(info.exec_line),
        ]
        for icon_name in dict.fromkeys(
            candidate for candidate in candidates if candidate
        ):
            pixbuf = self._try_load_icon_without_fallback(
                icon_name=icon_name,
                size=size,
            )
            if pixbuf is not None:
                self._icon_cache[key] = pixbuf
                return pixbuf
            log.bind(
                desktop_id=info.desktop_id,
                icon_name=icon_name,
                size=size,
            ).debug("Desktop icon candidate failed, trying next")

        pixbuf = self._try_load_fallback_icon(size=size)
        self._icon_cache[key] = pixbuf
        log.bind(
            desktop_id=info.desktop_id,
            size=size,
            used_fallback=pixbuf is not None,
        ).debug("Desktop icon fell back to generic")
        return pixbuf

    def load_gicon(self, gicon: Gio.Icon | None, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load a pixbuf directly from a Gio.Icon when available."""
        if gicon is None:
            return None
        cache_key = (f"gicon:{gicon.to_string()}", size)
        if cache_key in self._icon_cache:
            return self._icon_cache[cache_key]

        pixbuf = self._try_load_gicon(gicon=gicon, size=size)
        self._icon_cache[cache_key] = pixbuf
        return pixbuf

    def resolve_file(self, target: str, size: int) -> FileTargetInfo | None:
        """Resolve a file:// URI or local path into display metadata."""
        uri = normalize_file_target(target)
        if uri is None:
            return None
        try:
            gfile = Gio.File.new_for_uri(uri)
            info = gfile.query_info(
                "standard::display-name,standard::icon,standard::type,standard::content-type",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except GLib.Error as exc:
            log.bind(target=target, action="resolve_file").warning(
                f"Failed to query file info: {exc}"
            )
            return None

        icon = info.get_icon()
        icon_name = (
            "folder"
            if info.get_file_type() == Gio.FileType.DIRECTORY
            else "text-x-generic"
        )
        return FileTargetInfo(
            target=uri,
            name=info.get_display_name()
            or Path(unquote(urlparse(uri).path)).name
            or uri,
            icon_name=icon_name,
            icon=self.resolve_file_icon(
                target=uri,
                gicon=icon,
                content_type=info.get_content_type() or "",
                size=size,
                is_dir=info.get_file_type() == Gio.FileType.DIRECTORY,
            ),
            is_dir=info.get_file_type() == Gio.FileType.DIRECTORY,
        )

    def resolve_file_icon(
        self,
        *,
        target: str,
        gicon: Gio.Icon | None,
        content_type: str,
        size: int,
        is_dir: bool,
    ) -> GdkPixbuf.Pixbuf | None:
        """Resolve a file target icon, preferring image thumbnails when possible."""
        if not is_dir and content_type.lower().startswith("image/"):
            uri = normalize_file_target(target)
            if uri is not None:
                path = Path(unquote(urlparse(uri).path))
                if path.exists():
                    try:
                        return self._load_cached_file_icon(path=path, size=size)
                    except GLib.Error as exc:
                        log.bind(target=target, action="resolve_file_icon").debug(
                            "Failed to load image thumbnail %s: %s",
                            path,
                            exc,
                        )

        icon_name = "folder" if is_dir else "text-x-generic"
        return self.load_gicon(gicon=gicon, size=size) or self.load_icon(
            icon_name=icon_name,
            size=size,
        )

    def _load_cached_file_icon(
        self, *, path: Path, size: int
    ) -> GdkPixbuf.Pixbuf | None:
        stat = path.stat()
        cache_key = (str(path), size, int(stat.st_mtime_ns), int(stat.st_size))
        cached = self._file_icon_cache.pop(cache_key, None)
        if cached is not None:
            self._file_icon_cache[cache_key] = cached
            return cached

        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(path),
            size,
            size,
            True,
        )
        self._file_icon_cache[cache_key] = pixbuf
        while len(self._file_icon_cache) > FILE_ICON_CACHE_MAX_ENTRIES:
            self._file_icon_cache.pop(next(iter(self._file_icon_cache)))
        return pixbuf

    def default_directory_app_name(self) -> str | None:
        """Return the display name of the default app that opens folders."""
        try:
            app_info = Gio.AppInfo.get_default_for_type("inode/directory", False)
        except GLib.Error as exc:
            log.bind(action="default_directory_app_name").warning(
                "Failed to resolve default directory app: %s",
                exc,
            )
            return None
        if app_info is None:
            return None
        return app_info.get_display_name() or None

    def _build_wm_class_index(self) -> None:
        """Index installed desktop entries by WM_CLASS-like runtime aliases."""
        index: dict[str, desktop_entries.DesktopInfo] = {}
        seen_desktop_ids: set[str] = set()
        for desktop_dir in self._desktop_dirs:
            for path in desktop_dir.rglob(f"*{DESKTOP_SUFFIX}"):
                if not path.is_file():
                    continue
                desktop_id = path.relative_to(desktop_dir).as_posix()
                if desktop_id in seen_desktop_ids:
                    continue
                seen_desktop_ids.add(desktop_id)
                info = self.resolve(desktop_id=desktop_id, log_failures=False)
                if info is None:
                    continue
                for alias in desktop_entries.desktop_match_aliases(info):
                    index.setdefault(alias, info)
        self._wm_class_index = index

    def _try_load_gicon(self, gicon: Gio.Icon, size: int) -> GdkPixbuf.Pixbuf | None:
        theme = _create_icon_theme()

        lookup_by_gicon = getattr(theme, "lookup_by_gicon", None)
        if callable(lookup_by_gicon):
            try:
                info = lookup_by_gicon(gicon, size, Gtk.IconLookupFlags.FORCE_SIZE)
            except TypeError as exc:
                log.bind(action="load_gicon").debug(
                    "Theme lookup_by_gicon rejected %s: %s",
                    gicon.to_string(),
                    exc,
                )
                info = None
            if info is not None:
                try:
                    return info.load_icon()
                except GLib.Error as exc:
                    log.bind(action="load_gicon").debug(
                        "Theme gicon not found (%s): %s", gicon.to_string(), exc
                    )

        return self.load_icon(icon_name=gicon.to_string(), size=size)

    def _try_load_icon(self, icon_name: str, size: int) -> GdkPixbuf.Pixbuf | None:
        """Attempt to load icon from theme or file path."""
        pixbuf = self._try_load_icon_without_fallback(icon_name=icon_name, size=size)
        if pixbuf is not None:
            return pixbuf
        return self._try_load_fallback_icon(size=size)

    def _try_load_icon_without_fallback(
        self, icon_name: str, size: int
    ) -> GdkPixbuf.Pixbuf | None:
        theme = _create_icon_theme()

        for icon_path in _host_icon_file_candidates(icon_name):
            if not icon_path.exists():
                continue
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(icon_path), size, size, True
                )
            except GLib.Error as exc:
                log.bind(action="load_icon").debug(
                    "Failed to load icon file %s: %s",
                    icon_path,
                    exc,
                )

        for candidate in _theme_icon_candidates(icon_name):
            icon_info = theme.lookup_icon(
                candidate, size, Gtk.IconLookupFlags.FORCE_SIZE
            )
            if icon_info is None:
                continue
            try:
                return icon_info.load_icon()
            except GLib.Error as exc:
                log.bind(action="load_icon").debug(
                    "Theme icon not found (%s): %s",
                    candidate,
                    exc,
                )

        return None

    def _try_load_fallback_icon(self, *, size: int) -> GdkPixbuf.Pixbuf | None:
        theme = _create_icon_theme()
        icon_info = theme.lookup_icon(
            FALLBACK_ICON, size, Gtk.IconLookupFlags.FORCE_SIZE
        )
        if icon_info is None:
            return None
        try:
            return icon_info.load_icon()
        except GLib.Error as exc:
            log.bind(action="load_icon").warning(
                f"Failed to load fallback icon {FALLBACK_ICON}: {exc}"
            )
            return None


def get_actions(desktop_id: str) -> list[desktop_entries.DesktopAction]:
    """Return .desktop Actions entries (e.g. "New Window", "New Incognito Window")."""
    resolved = desktop_entries.resolve_app_info(
        desktop_id,
        action="get_actions",
        log_failures=False,
    )
    if resolved is None:
        path = desktop_entries.find_desktop_file(desktop_id)
        return desktop_entries.desktop_file_actions(path) if path is not None else []
    app_info = resolved.app_info
    result = []
    for action_id in app_info.list_actions():
        name = app_info.get_action_name(action_id)
        if name:
            result.append(desktop_entries.DesktopAction(action_id, name))
    return result


def launch_action(desktop_id: str, action_id: str) -> None:
    """Launch a named desktop action (from the .desktop [Desktop Action ...] group)."""
    resolved = desktop_entries.resolve_app_info(
        desktop_id,
        action="launch_action",
        log_failures=False,
    )
    if resolved is None:
        path = desktop_entries.find_desktop_file(desktop_id)
        if path is None:
            return
        exec_line = desktop_entries.desktop_file_action_exec(path, action_id)
        _launch_exec_line(
            desktop_id=desktop_id,
            exec_line=exec_line,
            desktop_file=path,
            action="launch_action",
        )
        return
    if desktop_entries.is_host_desktop_file(resolved.desktop_file):
        # Gio action launching would execute inside the sandbox. For host
        # desktop files, read the action Exec ourselves and delegate to host.
        exec_line = (
            desktop_entries.desktop_file_action_exec(resolved.desktop_file, action_id)
            if resolved.desktop_file is not None
            else ""
        )
        _launch_exec_line(
            desktop_id=desktop_id,
            exec_line=exec_line,
            desktop_file=resolved.desktop_file,
            action="launch_action",
        )
        return

    try:
        resolved.app_info.launch_action(action_id, None)
    except GLib.Error as exc:
        log.bind(desktop_id=desktop_id, action="launch_action").warning(
            "Failed to launch action %s for %s: %s",
            action_id,
            desktop_id,
            exc,
        )


def launch_new_window(desktop_id: str) -> None:
    """Open a new application window when the desktop entry exposes that action."""
    resolved = desktop_entries.resolve_app_info(
        desktop_id,
        action="launch_new_window",
        log_failures=False,
    )
    if resolved is None:
        launch(desktop_id=desktop_id)
        return
    try:
        if MiddleClickAction.NEW_WINDOW.value in resolved.app_info.list_actions():
            if desktop_entries.is_host_desktop_file(resolved.desktop_file):
                # Same rule as launch_action(): host desktop actions must not
                # be launched through sandbox Gio.
                exec_line = (
                    desktop_entries.desktop_file_action_exec(
                        resolved.desktop_file,
                        MiddleClickAction.NEW_WINDOW.value,
                    )
                    if resolved.desktop_file is not None
                    else ""
                )
                _launch_exec_line(
                    desktop_id=desktop_id,
                    exec_line=exec_line,
                    desktop_file=resolved.desktop_file,
                    action="launch_new_window",
                )
                return
            resolved.app_info.launch_action(MiddleClickAction.NEW_WINDOW.value, None)
            return
    except GLib.Error as exc:
        log.bind(desktop_id=desktop_id, action="launch_new_window").warning(
            "Failed to launch new-window action for %s: %s",
            desktop_id,
            exc,
        )
    launch(desktop_id=desktop_id)


def launch(desktop_id: str) -> None:
    """Launch an application by its desktop ID.

    Uses subprocess with start_new_session=True so the child gets its own
    session and process group. This prevents the child from receiving
    SIGHUP/SIGINT when the dock exits or the terminal sends Ctrl+C.
    """
    resolved = desktop_entries.resolve_desktop_launch(desktop_id, action="launch")
    if resolved is None:
        return
    _launch_exec_line(
        desktop_id=desktop_id,
        exec_line=resolved.exec_line,
        desktop_file=resolved.desktop_file,
        action="launch",
    )


def _launch_exec_line(
    *,
    desktop_id: str,
    exec_line: str,
    desktop_file: Path | None,
    action: str,
) -> None:
    if not exec_line:
        return
    cmd = re.sub(r"%[uUfFdDnNickvm]", "", exec_line).strip()
    if not cmd:
        return
    try:
        argv = [arg for arg in shlex.split(cmd, posix=True) if arg]
    except ValueError as e:
        log.bind(desktop_id=desktop_id, action="parse_exec").warning(
            f"Failed to parse launch command for {desktop_id}: {e}"
        )
        return
    if not argv:
        return
    if desktop_entries.is_host_desktop_file(desktop_file):
        # Host launchers may reference binaries and environment only available
        # outside the sandbox, so never execute their Exec line directly.
        host_argv = flatpak.host_command(argv)
        if host_argv is None:
            log.bind(desktop_id=desktop_id, action=action).warning(
                "Cannot launch host desktop file without flatpak-spawn: %s",
                desktop_file,
            )
            return
        argv = host_argv
    try:
        subprocess.Popen(
            argv,
            shell=False,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        log.bind(desktop_id=desktop_id, action=action).warning(
            f"Failed to launch {desktop_id}: {e}"
        )


def normalize_file_target(target: str) -> str | None:
    """Normalize a local path or file:// URI into a file:// URI."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path))
    elif parsed.scheme == "":
        path = Path(target).expanduser()
    else:
        return None
    try:
        return path.resolve().as_uri()
    except ValueError as exc:
        log.bind(target=target, action="normalize_file_target").debug(
            "Failed to normalize file target %s: %s",
            target,
            exc,
        )
        return None


def open_target(target: str) -> bool:
    """Open a local file, directory, or web URL with the default handler."""
    parsed = urlparse(target)
    if parsed.scheme in {"http", "https"}:
        uri = target
    else:
        uri = normalize_file_target(target)
    if uri is None:
        return False
    if is_flatpak() and urlparse(uri).scheme == "file":
        # Local files may be host-visible but not sandbox-openable; ask the
        # host desktop's gio to choose the default application.
        host_cmd = flatpak.host_command(["gio", "open", uri])
        if host_cmd is not None:
            try:
                subprocess.Popen(
                    host_cmd,
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except OSError as exc:
                log.bind(target=target, action="open_target").warning(
                    "Failed to open host target %s: %s",
                    target,
                    exc,
                )
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
        return True
    except GLib.Error as exc:
        log.bind(target=target, action="open_target").warning(
            f"Failed to open target {target}: {exc}"
        )
        return False
