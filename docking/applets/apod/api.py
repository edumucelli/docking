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

"""NASA APOD fetcher using only the Python standard library.

No API key required beyond the public ``DEMO_KEY`` identifier, which api.nasa.gov
accepts for low-traffic callers. The applet makes at most one JSON fetch and
one image download per day.
"""

from __future__ import annotations

import json
import urllib.request
from typing import NamedTuple

from docking.applets.apod import meta
from docking.applets.apod.state import ApodResult, build_page_url
from docking.core.paths import ensure_dir
from docking.log import get_logger, with_context
from docking.platform.environment import docking_cache_dir

log = with_context(get_logger(name="apod.api"), applet_id=meta.id)

API_URL = "https://api.nasa.gov/planetary/apod"
DEFAULT_API_KEY = "DEMO_KEY"
USER_AGENT = "docking-apod/1.0"
REQUEST_TIMEOUT_S = 20.0

CACHE_DIR = docking_cache_dir() / "apod"


class ApodError(NamedTuple):
    """Failure mode from an APOD fetch."""

    message: str


def fetch_today(*, api_key: str = DEFAULT_API_KEY) -> ApodResult | ApodError:
    """Fetch today's APOD JSON and download the preview image.

    Returns a fully populated :class:`ApodResult` on success, or an
    :class:`ApodError` with a user-facing message otherwise.
    """
    try:
        payload = _fetch_json(api_key=api_key)
    except Exception as exc:
        return ApodError(message=f"fetch failed: {exc}")

    if not isinstance(payload, dict):
        return ApodError(message="unexpected API response")

    media_type = str(payload.get("media_type", "image"))
    date_iso = str(payload.get("date", ""))
    title = str(payload.get("title", ""))
    explanation = str(payload.get("explanation", ""))
    copyright_ = str(payload.get("copyright", "") or "")
    page_url = build_page_url(date_iso)

    image_url = _pick_image_url(payload=payload)
    cached_path = ""
    if image_url:
        try:
            cached_path = _download_image(url=image_url, date_iso=date_iso)
        except Exception as exc:
            log.bind(action="image_download").debug("Image download failed: %s", exc)
            cached_path = ""

    return ApodResult(
        date=date_iso,
        title=title,
        explanation=explanation,
        media_type=media_type,
        image_url=image_url,
        page_url=page_url,
        copyright=copyright_,
        cached_path=cached_path,
    )


def _pick_image_url(*, payload: dict) -> str:
    """Prefer the plain-image URL; fall back to a YouTube thumbnail."""
    media_type = str(payload.get("media_type", "image"))
    if media_type == "image":
        return str(payload.get("url", "") or payload.get("hdurl", ""))
    thumb = payload.get("thumbnail_url")
    if isinstance(thumb, str) and thumb:
        return thumb
    return ""


def _fetch_json(*, api_key: str) -> dict:
    req = urllib.request.Request(
        f"{API_URL}?api_key={api_key}&thumbs=true",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _download_image(*, url: str, date_iso: str) -> str:
    ensure_dir(CACHE_DIR)
    suffix = _suffix_for_url(url)
    path = CACHE_DIR / f"{date_iso or 'unknown'}{suffix}"
    if path.exists() and path.stat().st_size > 0:
        return str(path)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = path.with_suffix(path.suffix + ".part")
    with (
        urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp,
        tmp.open("wb") as out,
    ):
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(path)
    return str(path)


def _suffix_for_url(url: str) -> str:
    lowered = url.lower().split("?", 1)[0]
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        if lowered.endswith(ext):
            return ext
    return ".jpg"
