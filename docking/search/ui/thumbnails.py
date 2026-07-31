"""Bounded, modification-aware image loading for Global Search previews."""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

from docking.search.preview import MAX_IMAGE_PREVIEW_FILE_SIZE

MAX_IMAGE_DIMENSION = 32_768
MAX_IMAGE_PIXELS = 40_000_000
DEFAULT_CACHE_ENTRIES = 128


@dataclass(frozen=True, slots=True)
class LoadedSearchImage:
    pixbuf: GdkPixbuf.Pixbuf
    width: int
    height: int
    format_name: str
    file_size: int


class SearchImageCache:
    """Decode each unchanged image at a requested size only once."""

    def __init__(self) -> None:
        self._max_entries = DEFAULT_CACHE_ENTRIES
        self._entries: OrderedDict[
            tuple[str, int, int, int, int],
            LoadedSearchImage | None,
        ] = OrderedDict()
        self._lock = threading.RLock()

    def load(
        self,
        *,
        path: str,
        max_width: int,
        max_height: int,
    ) -> LoadedSearchImage | None:
        file_path = Path(path)
        try:
            stat = file_path.stat()
        except OSError:
            return None
        if (
            not file_path.is_file()
            or stat.st_size > MAX_IMAGE_PREVIEW_FILE_SIZE
            or max_width <= 0
            or max_height <= 0
        ):
            return None
        cache_key = (
            str(file_path),
            stat.st_mtime_ns,
            stat.st_size,
            int(max_width),
            int(max_height),
        )
        with self._lock:
            if cache_key in self._entries:
                cached = self._entries[cache_key]
                self._entries.move_to_end(cache_key)
                return cached

            loaded = _load_image(
                path=file_path,
                file_size=stat.st_size,
                max_width=max_width,
                max_height=max_height,
            )
            self._entries[cache_key] = loaded
            self._entries.move_to_end(cache_key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return loaded

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _load_image(
    *,
    path: Path,
    file_size: int,
    max_width: int,
    max_height: int,
) -> LoadedSearchImage | None:
    try:
        image_format, width, height = GdkPixbuf.Pixbuf.get_file_info(str(path))
        if (
            image_format is None
            or width <= 0
            or height <= 0
            or width > MAX_IMAGE_DIMENSION
            or height > MAX_IMAGE_DIMENSION
            or width * height > MAX_IMAGE_PIXELS
        ):
            return None
        scale = min(max_width / width, max_height / height, 1.0)
        shown_width = max(1, round(width * scale))
        shown_height = max(1, round(height * scale))
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(path),
            shown_width,
            shown_height,
            True,
        )
        if pixbuf is None:
            return None
        oriented = pixbuf.apply_embedded_orientation()
        if oriented is not None:
            pixbuf = oriented
        format_name = str(image_format.get_name() or "").upper()
    except (GLib.Error, OSError, ValueError):
        return None
    return LoadedSearchImage(
        pixbuf=pixbuf,
        width=width,
        height=height,
        format_name=format_name,
        file_size=file_size,
    )


__all__ = [
    "DEFAULT_CACHE_ENTRIES",
    "MAX_IMAGE_DIMENSION",
    "MAX_IMAGE_PIXELS",
    "LoadedSearchImage",
    "SearchImageCache",
]
