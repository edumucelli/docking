"""Pure state and formatting logic for the APOD applet."""

from __future__ import annotations

import textwrap
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

from docking.i18n import _

# Maximum characters per tooltip line. Chosen so the wrapped explanation
# stays comfortably narrower than a typical dock tooltip width.
TOOLTIP_WRAP_WIDTH = 60

# How often to re-check the APOD feed when the cached copy is from today.
# Once the date flips over, the applet fetches the new image regardless.
REFRESH_CHECK_INTERVAL_S = 3600  # 1 hour

# Polite retry window after a network failure.
RETRY_ON_ERROR_S = 600  # 10 minutes


class ApodResult(NamedTuple):
    """Metadata for one Astronomy Picture of the Day."""

    date: str  # ISO date (YYYY-MM-DD, NASA UTC-ish)
    title: str
    explanation: str
    media_type: str  # "image" or "video"
    image_url: str  # high-res image or YouTube thumbnail (may be empty)
    page_url: str  # permalink on apod.nasa.gov
    copyright: str
    cached_path: str  # local path to the downloaded image or empty


@dataclass(frozen=True, slots=True)
class ApodPrefs:
    """Persisted APOD applet preferences."""

    last_result: ApodResult | None = None


def build_page_url(date: str) -> str:
    """Derive the classic ``apXXYYZZ.html`` permalink from an ISO date."""
    try:
        year, month, day = date.split("-")
        yy = year[-2:]
    except ValueError:
        return "https://apod.nasa.gov/apod/astropix.html"
    return f"https://apod.nasa.gov/apod/ap{yy}{month}{day}.html"


def format_explanation(
    text: str,
    *,
    max_chars: int = 320,
    wrap: int = TOOLTIP_WRAP_WIDTH,
) -> str:
    """Truncate the NASA explanation and wrap so tooltip lines stay narrow."""
    cleaned = " ".join(text.split())
    if len(cleaned) > max_chars:
        clipped = cleaned[: max_chars - 1]
        # Try to cut on the last space so we do not chop a word.
        last_space = clipped.rfind(" ")
        if last_space > max_chars // 2:
            clipped = clipped[:last_space]
        cleaned = clipped + "…"
    return textwrap.fill(
        cleaned,
        width=wrap,
        break_long_words=False,
        break_on_hyphens=False,
    )


def build_tooltip(*, result: ApodResult | None, error: str | None) -> str:
    lines = [_("Astronomy Picture of the Day")]
    if error and result is None:
        lines.append(_("Error: {msg}").format(msg=error))
        return "\n".join(lines)
    if result is None:
        lines.append(_("Loading..."))
        return "\n".join(lines)
    lines.append(result.date)
    if result.title:
        lines.append(result.title)
    if result.copyright:
        lines.append(_("(c) {who}").format(who=result.copyright.strip()))
    if result.explanation:
        lines.append("")
        lines.append(format_explanation(result.explanation))
    return "\n".join(lines)


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> ApodPrefs:
    if not prefs:
        return ApodPrefs()
    raw = prefs.get("last_result")
    if not isinstance(raw, Mapping):
        return ApodPrefs()
    try:
        result = ApodResult(
            date=str(raw.get("date", "")),
            title=str(raw.get("title", "")),
            explanation=str(raw.get("explanation", "")),
            media_type=str(raw.get("media_type", "image")),
            image_url=str(raw.get("image_url", "")),
            page_url=str(raw.get("page_url", "")),
            copyright=str(raw.get("copyright", "")),
            cached_path=str(raw.get("cached_path", "")),
        )
    except (TypeError, ValueError):
        return ApodPrefs()
    if not result.date:
        return ApodPrefs()
    return ApodPrefs(last_result=result)


def prefs_payload(*, result: ApodResult | None) -> dict[str, Any]:
    if result is None:
        return {}
    return {
        "last_result": {
            "date": result.date,
            "title": result.title,
            "explanation": result.explanation,
            "media_type": result.media_type,
            "image_url": result.image_url,
            "page_url": result.page_url,
            "copyright": result.copyright,
            "cached_path": result.cached_path,
        }
    }
