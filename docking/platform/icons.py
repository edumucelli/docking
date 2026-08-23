"""GTK icon loading and presentation-oriented file icon fallbacks."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from docking.log import get_logger, with_context
from docking.platform.applications import entries as desktop_entries
from docking.platform.applications.constants import FALLBACK_ICON, GNOME_APP_PREFIX
from docking.platform.applications.projections import dock_metadata
from docking.platform.applications.types import ApplicationInfo

FILE_ICON_CACHE_MAX_ENTRIES = 256
HOST_XDG_DATA_DIRS = desktop_entries.HOST_XDG_DATA_DIRS
HOST_PIXMAP_DIRS = tuple(f"{data_dir}/pixmaps" for data_dir in HOST_XDG_DATA_DIRS)
HOST_FILESYSTEM_ROOT = desktop_entries.HOST_FILESYSTEM_ROOT
ICON_FILE_EXTENSIONS = (".png", ".svg", ".xpm")

FileTargetNormalizer = Callable[[str], str | None]
log = with_context(get_logger(name="icons"))


def fallback_file_icon_name(*, is_dir: bool) -> str:
    """Return the theme icon name for file targets without richer metadata."""
    return "folder" if is_dir else "text-x-generic"


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
    icon_path = Path(icon_name)
    if icon_path.parent == Path() and icon_path.suffix.lower() in ICON_FILE_EXTENSIONS:
        candidates.append(icon_path.stem)
    if icon_name.startswith(GNOME_APP_PREFIX):
        name = icon_name.removeprefix(GNOME_APP_PREFIX)
        candidates.append(f"gnome-{name.replace('.', '-').lower()}")
        candidates.append(name.replace(".", "-").lower())
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _normalize_file_target_for_icon(target: str) -> str | None:
    """Normalize a local path or file URI without importing target services."""
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
    except ValueError:
        return None


def _application_icon_fields(info: ApplicationInfo) -> tuple[str, str, str]:
    metadata = dock_metadata(info)
    return metadata.desktop_id, metadata.icon_name, metadata.exec_line


class IconLoader:
    """Load and cache desktop, theme, GIcon, and file thumbnail icons."""

    def __init__(
        self,
        *,
        normalize_file_target: FileTargetNormalizer | None = None,
    ) -> None:
        self._normalize_file_target = (
            normalize_file_target or _normalize_file_target_for_icon
        )
        self._icon_cache: dict[tuple[str, int], GdkPixbuf.Pixbuf | None] = {}
        self._file_icon_cache: dict[
            tuple[str, int, int, int], GdkPixbuf.Pixbuf | None
        ] = {}

    def load_icon(self, icon_name: str, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load an icon by name at the given size, with caching."""
        key = (icon_name, size)
        if key in self._icon_cache:
            return self._icon_cache[key]

        pixbuf = self._try_load_icon(icon_name=icon_name, size=size)
        self._icon_cache[key] = pixbuf
        return pixbuf

    def load_icon_file(self, path: Path, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load an absolute image path as an icon without a generic fallback."""
        if not path.is_absolute() or not path.is_file():
            return None
        try:
            return self._load_cached_file_icon(path=path, size=size)
        except (OSError, GLib.Error) as exc:
            log.bind(action="load_icon_file", path=str(path), size=size).debug(
                "Failed to load custom icon file: %s",
                exc,
            )
            return None

    def load_desktop_icon(
        self,
        info: ApplicationInfo,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        """Load an application icon from canonical metadata with fallbacks."""
        desktop_id, icon_name, exec_line = _application_icon_fields(info)
        key = (f"desktop:{desktop_id}:{icon_name}:{exec_line}", size)
        if key in self._icon_cache:
            return self._icon_cache[key]

        candidates = [
            icon_name,
            desktop_entries.normalized_exec_basename(exec_line),
        ]
        for candidate in dict.fromkeys(value for value in candidates if value):
            pixbuf = self._try_load_icon_without_fallback(
                icon_name=candidate,
                size=size,
            )
            if pixbuf is not None:
                self._icon_cache[key] = pixbuf
                return pixbuf
            log.bind(
                desktop_id=desktop_id,
                icon_name=candidate,
                size=size,
            ).debug("Desktop icon candidate failed, trying next")

        pixbuf = self._try_load_fallback_icon(size=size)
        self._icon_cache[key] = pixbuf
        log.bind(
            desktop_id=desktop_id,
            size=size,
            used_fallback=pixbuf is not None,
        ).debug("Desktop icon fell back to generic")
        return pixbuf

    def load_gicon(
        self,
        gicon: Gio.Icon | None,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        """Load a pixbuf directly from a Gio.Icon when available."""
        if gicon is None:
            return None
        cache_key = (f"gicon:{gicon.to_string()}", size)
        if cache_key in self._icon_cache:
            return self._icon_cache[cache_key]

        pixbuf = self._try_load_gicon(gicon=gicon, size=size)
        self._icon_cache[cache_key] = pixbuf
        return pixbuf

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
            uri = self._normalize_file_target(target)
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

        icon_name = fallback_file_icon_name(is_dir=is_dir)
        return self.load_gicon(gicon=gicon, size=size) or self.load_icon(
            icon_name=icon_name,
            size=size,
        )

    def _load_cached_file_icon(
        self,
        *,
        path: Path,
        size: int,
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

    def _try_load_gicon(
        self,
        gicon: Gio.Icon,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
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
                        "Theme gicon not found (%s): %s",
                        gicon.to_string(),
                        exc,
                    )

        icon_name = gicon.to_string()
        if icon_name:
            return self.load_icon(icon_name=icon_name, size=size)
        return None

    def _try_load_icon(
        self,
        icon_name: str,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        """Attempt to load an icon from a theme or file path."""
        pixbuf = self._try_load_icon_without_fallback(icon_name=icon_name, size=size)
        if pixbuf is not None:
            return pixbuf
        return self._try_load_fallback_icon(size=size)

    def _try_load_icon_without_fallback(
        self,
        icon_name: str,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        theme = _create_icon_theme()

        for icon_path in _host_icon_file_candidates(icon_name):
            if not icon_path.exists():
                continue
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(icon_path),
                    size,
                    size,
                    True,
                )
            except GLib.Error as exc:
                log.bind(action="load_icon").debug(
                    "Failed to load icon file %s: %s",
                    icon_path,
                    exc,
                )

        for candidate in _theme_icon_candidates(icon_name):
            icon_info = theme.lookup_icon(
                candidate,
                size,
                Gtk.IconLookupFlags.FORCE_SIZE,
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
            FALLBACK_ICON,
            size,
            Gtk.IconLookupFlags.FORCE_SIZE,
        )
        if icon_info is None:
            return None
        try:
            return icon_info.load_icon()
        except GLib.Error as exc:
            log.bind(action="load_icon").warning(
                "Failed to load fallback icon %s: %s",
                FALLBACK_ICON,
                exc,
            )
            return None


__all__ = [
    "FALLBACK_ICON",
    "IconLoader",
    "fallback_file_icon_name",
]
