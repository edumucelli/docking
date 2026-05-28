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

"""Pure logic for the Last.fm applet: prefs, URL building, JSON parsing."""

from __future__ import annotations

import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

DEFAULT_MAX_ENTRIES = 10
MIN_MAX_ENTRIES = 1
MAX_MAX_ENTRIES = 50
DEFAULT_REFRESH_SECONDS = 60

# Service identifiers. Libre.fm implements the Last.fm 2.0 web-services
# protocol, so the request/response shape is identical; only the host
# changes. Read methods on Libre.fm don't require an api_key.
LASTFM_SERVICE = "lastfm"
LIBREFM_SERVICE = "librefm"
DEFAULT_SERVICE = LASTFM_SERVICE
ALLOWED_SERVICES: frozenset[str] = frozenset({LASTFM_SERVICE, LIBREFM_SERVICE})

LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0/"
LASTFM_USER_URL_BASE = "https://www.last.fm/user/"
LIBREFM_API_BASE = "https://libre.fm/2.0/"
LIBREFM_USER_URL_BASE = "https://libre.fm/user/"

_API_BASES: dict[str, str] = {
    LASTFM_SERVICE: LASTFM_API_BASE,
    LIBREFM_SERVICE: LIBREFM_API_BASE,
}
_USER_URL_BASES: dict[str, str] = {
    LASTFM_SERVICE: LASTFM_USER_URL_BASE,
    LIBREFM_SERVICE: LIBREFM_USER_URL_BASE,
}
_SERVICE_LABELS: dict[str, str] = {
    LASTFM_SERVICE: "Last.fm",
    LIBREFM_SERVICE: "Libre.fm",
}

# Last.fm marks missing album art with this well-known hash.
NOT_FOUND_IMAGE_HASH = "2a96cbd8b46e442fc41c2b86b821562f"

# Image size keys returned by the Last.fm API, ordered from best to worst.
IMAGE_SIZE_CASCADE: tuple[str, ...] = (
    "extralarge",
    "large",
    "medium",
    "small",
)


def normalize_service(value: object) -> str:
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in ALLOWED_SERVICES:
            return candidate
    return DEFAULT_SERVICE


def api_base_for(service: str) -> str:
    return _API_BASES.get(service, LASTFM_API_BASE)


def profile_base_for(service: str) -> str:
    return _USER_URL_BASES.get(service, LASTFM_USER_URL_BASE)


def service_display_name(service: str) -> str:
    return _SERVICE_LABELS.get(service, _SERVICE_LABELS[LASTFM_SERVICE])


class PlayedTrack(NamedTuple):
    """A scrobbled (or currently playing) track from Last.fm.

    Image URLs are kept as a dict so the renderer can pick the best size.
    """

    track_name: str
    artist: str
    album: str
    track_url: str
    image_urls: Mapping[str, str]
    timestamp: int | None  # Unix seconds; None when now_playing
    is_now_playing: bool
    is_loved: bool


@dataclass(frozen=True)
class LastfmPrefs:
    """Persisted Last.fm/Libre.fm applet preferences."""

    api_key: str = ""
    username: str = ""
    max_entries: int = DEFAULT_MAX_ENTRIES
    rounded_corners: bool = False
    service: str = DEFAULT_SERVICE

    @property
    def is_configured(self) -> bool:
        if not self.username:
            return False
        # Libre.fm read methods don't require an API key.
        if self.service == LIBREFM_SERVICE:
            return True
        return bool(self.api_key)


def prefs_from_mapping(data: Mapping[str, Any] | None) -> LastfmPrefs:
    """Build a ``LastfmPrefs`` from a (possibly partial) prefs mapping."""
    if not data:
        return LastfmPrefs()
    try:
        max_entries = int(data.get("max_entries", DEFAULT_MAX_ENTRIES))
    except (TypeError, ValueError):
        max_entries = DEFAULT_MAX_ENTRIES
    max_entries = max(MIN_MAX_ENTRIES, min(MAX_MAX_ENTRIES, max_entries))
    return LastfmPrefs(
        api_key=str(data.get("api_key", "")).strip(),
        username=str(data.get("username", "")).strip(),
        max_entries=max_entries,
        rounded_corners=bool(data.get("rounded_corners", False)),
        service=normalize_service(data.get("service", DEFAULT_SERVICE)),
    )


def prefs_payload(prefs: LastfmPrefs) -> dict[str, Any]:
    """Serialize ``LastfmPrefs`` back into a JSON-friendly dict."""
    return {
        "api_key": prefs.api_key,
        "username": prefs.username,
        "max_entries": prefs.max_entries,
        "rounded_corners": prefs.rounded_corners,
        "service": prefs.service,
    }


def build_recent_tracks_url(
    *,
    api_key: str,
    username: str,
    limit: int,
    service: str = DEFAULT_SERVICE,
) -> str:
    """Build the URL for ``user.getrecenttracks`` (JSON, extended=1).

    ``api_key`` is omitted from the query when empty so Libre.fm calls
    (which don't require auth for read methods) stay clean.
    """
    params: dict[str, str] = {
        "method": "user.getrecenttracks",
        "user": username,
        "format": "json",
        "limit": str(max(1, int(limit))),
        "extended": "1",
    }
    if api_key:
        params["api_key"] = api_key
    return f"{api_base_for(service)}?{urllib.parse.urlencode(params)}"


def profile_url(service: str, username: str) -> str:
    return f"{profile_base_for(service)}{urllib.parse.quote_plus(username)}"


def parse_recent_tracks(
    payload: Any, limit: int, *, now_playing: bool = True
) -> list[PlayedTrack]:
    """Parse a Last.fm ``user.getrecenttracks`` JSON response.

    ``now_playing``: when False the in-progress track (if any) is skipped.
    Last.fm's API has an off-by-one quirk where the now-playing track can
    push the page over ``limit``; we trim to ``limit`` items regardless.
    """
    if not isinstance(payload, Mapping):
        return []
    if "error" in payload:
        return []
    recent = payload.get("recenttracks")
    if not isinstance(recent, Mapping):
        return []
    raw_tracks = recent.get("track")
    if raw_tracks is None:
        return []
    if isinstance(raw_tracks, Mapping):  # API returns a single object for n=1
        raw_tracks = [raw_tracks]
    if not isinstance(raw_tracks, list):
        return []

    out: list[PlayedTrack] = []
    for raw in raw_tracks:
        if not isinstance(raw, Mapping):
            continue
        parsed = _parse_track(raw)
        if parsed is None:
            continue
        if parsed.is_now_playing and not now_playing:
            continue
        out.append(parsed)
        if len(out) >= max(1, limit):
            break
    return out


def _parse_track(raw: Mapping[str, Any]) -> PlayedTrack | None:
    name = _str(raw.get("name"))
    if not name:
        return None

    artist_obj = raw.get("artist")
    if isinstance(artist_obj, Mapping):
        artist = _str(artist_obj.get("name")) or _str(artist_obj.get("#text"))
    else:
        artist = _str(artist_obj)

    album_obj = raw.get("album")
    if isinstance(album_obj, Mapping):
        album = _str(album_obj.get("#text"))
    else:
        album = _str(album_obj)

    image_urls = _parse_images(raw.get("image"))

    attrs = raw.get("@attr")
    is_now_playing = bool(
        isinstance(attrs, Mapping)
        and str(attrs.get("nowplaying", "")).lower() == "true"
    )

    timestamp: int | None = None
    date_obj = raw.get("date")
    if isinstance(date_obj, Mapping):
        try:
            timestamp = int(date_obj.get("uts", ""))
        except (TypeError, ValueError):
            timestamp = None

    is_loved = _str(raw.get("loved")) == "1"

    return PlayedTrack(
        track_name=name,
        artist=artist,
        album=album,
        track_url=_str(raw.get("url")),
        image_urls=image_urls,
        timestamp=timestamp,
        is_now_playing=is_now_playing,
        is_loved=is_loved,
    )


def _parse_images(raw: Any) -> dict[str, str]:
    images: dict[str, str] = {}
    if not isinstance(raw, list):
        return images
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        size = _str(entry.get("size"))
        url = _str(entry.get("#text"))
        if size and url:
            images[size] = url
    return images


def best_image_url(track: PlayedTrack) -> str:
    """Return the highest-resolution image URL the API returned."""
    for size in IMAGE_SIZE_CASCADE:
        url = track.image_urls.get(size, "")
        if url:
            return url
    return ""


def is_placeholder_image(url: str) -> bool:
    """True for Last.fm's "image missing" sentinel asset."""
    return NOT_FOUND_IMAGE_HASH in url


def format_relative_time(timestamp: int | None, *, now: int | None = None) -> str:
    """Render a Unix timestamp as a localized relative-time label."""
    if not timestamp:
        return ""
    if now is None:
        now = int(time.time())
    diff = max(0, now - int(timestamp))
    if diff < 60:
        return "Just now"
    minutes = diff // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 7:
        return f"{days} day{'s' if days != 1 else ''} ago"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    months = days // 30
    if months < 12:
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years != 1 else ''} ago"


def tooltip_for(prefs: LastfmPrefs, track: PlayedTrack | None) -> str:
    """Tooltip shown on the dock icon."""
    label = service_display_name(prefs.service)
    if not prefs.is_configured:
        return f"{label}: not configured"
    if track is None:
        return f"{label}: {prefs.username}"
    arrow = " ♪ " if track.is_now_playing else " - "
    return f"{track.artist}{arrow}{track.track_name}"


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


@dataclass
class ImageCache:
    """Bounded URL-keyed cache of raw image bytes.

    Capacity = ``max_entries * 3`` so a menu rebuild after a fetch doesn't
    immediately evict everything. Eviction is "clear when full" since the
    working set is a few MB and access is dominated by recent items.
    """

    max_entries: int = DEFAULT_MAX_ENTRIES
    _entries: dict[str, bytes] = field(default_factory=dict)

    @property
    def capacity(self) -> int:
        return max(MIN_MAX_ENTRIES, self.max_entries) * 3

    def get(self, url: str) -> bytes | None:
        return self._entries.get(url)

    def set(self, url: str, data: bytes) -> None:
        if len(self._entries) >= self.capacity:
            self._entries.clear()
        self._entries[url] = data

    def resize(self, max_entries: int) -> None:
        self.max_entries = max(MIN_MAX_ENTRIES, int(max_entries))
        if len(self._entries) > self.capacity:
            self._entries.clear()

    def clear(self) -> None:
        self._entries.clear()
