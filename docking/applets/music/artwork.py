"""Album artwork resolution and in-memory cache for music applet."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import NamedTuple

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

from docking.log import get_logger, with_context

from .state import MusicState

_MISS_RETRY_S = 60.0
_REMOTE_TIMEOUT_S = 2.5
_REMOTE_MAX_BYTES = 4 * 1024 * 1024
_ITUNES_ENDPOINT = "https://itunes.apple.com/search"
_LOCAL_FILENAMES: tuple[str, ...] = (
    "cover.jpg",
    "cover.jpeg",
    "cover.png",
    "folder.jpg",
    "folder.jpeg",
    "folder.png",
    "front.jpg",
    "front.jpeg",
    "front.png",
    "album.jpg",
    "album.jpeg",
    "album.png",
)
_log = with_context(get_logger("music_artwork"), applet_id="music")


class _CacheEntry(NamedTuple):
    pixbuf: GdkPixbuf.Pixbuf
    bytes_estimate: int


class CoverArtResolver:
    """Resolve album art from metadata/local files/network with LRU cache."""

    def __init__(
        self,
        max_entries: int = 96,
        max_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._cache_bytes = 0
        self._recent_misses: dict[str, float] = {}

    def resolve(self, state: MusicState) -> GdkPixbuf.Pixbuf | None:
        """Resolve and cache artwork for a given music state."""
        key = self._cache_key(state=state)
        if not key:
            return None

        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached.pixbuf

        miss_ts = self._recent_misses.get(key, 0.0)
        if miss_ts and (time.monotonic() - miss_ts) < _MISS_RETRY_S:
            return None

        pixbuf = self._resolve_uncached(state=state)
        if pixbuf is None:
            self._recent_misses[key] = time.monotonic()
            return None

        self._insert_cache(key=key, pixbuf=pixbuf)
        self._recent_misses.pop(key, None)
        return pixbuf

    def _cache_key(self, state: MusicState) -> str:
        if not state.available:
            return ""
        parts = [
            state.player_name,
            state.artist,
            state.album,
            state.title,
            state.art_url,
            state.track_url,
        ]
        return "|".join(parts).strip("|")

    def _resolve_uncached(self, state: MusicState) -> GdkPixbuf.Pixbuf | None:
        if state.art_url:
            pixbuf = self._load_from_uri(uri=state.art_url)
            if pixbuf is not None:
                return pixbuf

        local_cover = self._find_local_cover_path(track_url=state.track_url)
        if local_cover is not None:
            pixbuf = self._load_from_path(path=local_cover)
            if pixbuf is not None:
                return pixbuf

        online_url = self._lookup_online_cover_url(
            artist=state.artist,
            album=state.album,
            title=state.title,
        )
        if online_url:
            return self._load_from_uri(uri=online_url)

        return None

    def _find_local_cover_path(self, track_url: str) -> Path | None:
        track_path = self._path_from_uri_or_path(value=track_url)
        if track_path is None:
            return None
        folder = track_path if track_path.is_dir() else track_path.parent
        if not folder.exists() or not folder.is_dir():
            return None

        for filename in _LOCAL_FILENAMES:
            candidate = folder / filename
            if candidate.is_file():
                return candidate

        lowered = {
            entry.name.lower(): entry for entry in folder.iterdir() if entry.is_file()
        }
        for filename in _LOCAL_FILENAMES:
            name = lowered.get(filename.lower())
            if name is not None:
                return name
        return None

    def _lookup_online_cover_url(
        self,
        artist: str,
        album: str,
        title: str,
    ) -> str | None:
        query = " ".join(part for part in [artist, album] if part).strip()
        if not query:
            query = title.strip()
        if not query:
            return None

        params = urllib.parse.urlencode(
            {
                "term": query,
                "media": "music",
                "entity": "song",
                "limit": 1,
            }
        )
        url = f"{_ITUNES_ENDPOINT}?{params}"
        payload = self._download_bytes(uri=url, require_image=False)
        if payload is None:
            return None
        try:
            data = json.loads(payload.decode("utf-8"))
            results = data.get("results", [])
            if not results:
                return None
            art_url = str(results[0].get("artworkUrl100", "")).strip()
            if not art_url:
                return None
            return art_url.replace("100x100bb", "600x600bb")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _log.debug("Failed to decode iTunes artwork lookup response: %s", exc)
            return None

    def _load_from_path(self, path: Path) -> GdkPixbuf.Pixbuf | None:
        try:
            return GdkPixbuf.Pixbuf.new_from_file(str(path))
        except (GLib.Error, FileNotFoundError, OSError) as exc:
            _log.debug("Failed to load artwork from path %s: %s", path, exc)
            return None

    def _load_from_uri(self, uri: str) -> GdkPixbuf.Pixbuf | None:
        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme in ("", "file"):
            path = self._path_from_uri_or_path(value=uri)
            if path is None:
                return None
            return self._load_from_path(path=path)
        if parsed.scheme in ("http", "https"):
            payload = self._download_bytes(uri=uri, require_image=True)
            if payload is None:
                return None
            return self._pixbuf_from_bytes(payload=payload)
        return None

    def _download_bytes(self, uri: str, require_image: bool) -> bytes | None:
        request = urllib.request.Request(
            uri,
            headers={"User-Agent": "Docking/0.1 MusicApplet"},
        )
        try:
            with urllib.request.urlopen(request, timeout=_REMOTE_TIMEOUT_S) as response:
                content_type = ""
                if response.headers is not None:
                    content_type = (response.headers.get_content_type() or "").lower()
                if (
                    require_image
                    and content_type
                    and not content_type.startswith("image/")
                ):
                    return None

                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _REMOTE_MAX_BYTES:
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            _log.debug("Failed to download artwork from %s: %s", uri, exc)
            return None

    def _pixbuf_from_bytes(self, payload: bytes) -> GdkPixbuf.Pixbuf | None:
        try:
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(payload)
            loader.close()
            return loader.get_pixbuf()
        except GLib.Error as exc:
            _log.debug("Failed to decode artwork payload into pixbuf: %s", exc)
            return None

    def _path_from_uri_or_path(self, value: str) -> Path | None:
        raw = value.strip()
        if not raw:
            return None
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme == "file":
            return Path(urllib.parse.unquote(parsed.path))
        if parsed.scheme == "":
            return Path(raw)
        return None

    def _insert_cache(self, key: str, pixbuf: GdkPixbuf.Pixbuf) -> None:
        size = self._pixbuf_size_bytes(pixbuf=pixbuf)
        self._cache[key] = _CacheEntry(pixbuf=pixbuf, bytes_estimate=size)
        self._cache.move_to_end(key)
        self._cache_bytes += size
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        while self._cache and (
            len(self._cache) > self._max_entries or self._cache_bytes > self._max_bytes
        ):
            _, removed = self._cache.popitem(last=False)
            self._cache_bytes = max(0, self._cache_bytes - removed.bytes_estimate)

    def _pixbuf_size_bytes(self, pixbuf: GdkPixbuf.Pixbuf) -> int:
        channels = pixbuf.get_n_channels() or 4
        return pixbuf.get_width() * pixbuf.get_height() * channels
