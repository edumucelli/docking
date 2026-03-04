"""Moon phase data from briancasey.org — pure helpers, no GTK dependency.

This parser is a direct descendant of the original Moon applet written by
Eduardo Mucelli for Cairo-Dock (circa 2012). That applet parsed the same
website using SGMLParser to extract moon phase images and illumination data.

Over a decade later, the same website still serves the same HTML structure.
The parsing logic below is adapted from MoonCalendarParser.py in the original
cairo-dock-plug-ins-extras/Moon directory — coming full circle from a Cairo-Dock
applet created years ago back into a dock built from scratch.
"""

from __future__ import annotations

import re
from datetime import date
from typing import NamedTuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from docking.log import get_logger

_log = get_logger(name="moon.state")

_MOON_URL = "https://briancasey.org/artifacts/astro/moon.cgi"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class MoonData(NamedTuple):
    """Parsed moon phase data for a single day."""

    image_name: str  # e.g. "moon10b" (without extension)
    illumination: float  # 0.0–1.0
    description: str  # e.g. "1.8 days after full moon"
    date_label: str  # e.g. "Mar 4, 2026"


def fetch_moon(day: date | None = None) -> MoonData | None:
    """Fetch moon phase data from briancasey.org.

    This is the same website and parameter format used by the original
    Cairo-Dock Moon applet (MoonCalendarParser.py). The HTML structure
    has remained stable for over a decade.

    Returns None on any network/parse error.
    """
    if day is None:
        day = date.today()

    params = urlencode({"year": day.year, "month": day.month, "day": day.day})
    req = Request(
        _MOON_URL,
        data=params.encode("utf-8"),
        headers={"User-Agent": _USER_AGENT},
    )

    try:
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        return _parse_moon_html(html=html)
    except (OSError, ValueError) as exc:
        _log.warning("Failed to fetch moon data, using offline: %s", exc)
        from docking.applets.moon.offline import fetch_moon_offline

        return fetch_moon_offline(d=day)


# -- HTML parsing (adapted from the original MoonCalendarParser.py) -----------
#
# The original used SGMLParser (removed in Python 3). We use simple regexes
# on the same HTML structure — the website hasn't changed its format.

_IMG_RE = re.compile(r'<img\s+src="images/(moon\d+[ab])\.gif"', re.IGNORECASE)
_ILLUM_RE = re.compile(r"Illuminated Fraction:\s*([\d.]+)")
_DESC_RE = re.compile(r"([\d.]+\s+days?\s+(?:after|before)\s+\w[\w\s]*)")
_DATE_RE = re.compile(r"The Moon for\s+(.+?)\s*</font>", re.IGNORECASE)


def _parse_moon_html(html: str) -> MoonData | None:
    """Extract moon data from the briancasey.org response HTML.

    Mirrors the parsing logic from the original Cairo-Dock MoonCalendarParser,
    adapted from SGMLParser tag handlers to regex extraction.
    """
    img_match = _IMG_RE.search(html)
    illum_match = _ILLUM_RE.search(html)
    date_match = _DATE_RE.search(html)
    desc_match = _DESC_RE.search(html)

    if not img_match or not illum_match:
        return None

    return MoonData(
        image_name=img_match.group(1),
        illumination=float(illum_match.group(1)),
        description=desc_match.group(1).strip() if desc_match else "",
        date_label=date_match.group(1).strip() if date_match else "",
    )


def phase_name(illumination: float, description: str) -> str:
    """Human-readable phase name from illumination and description text."""
    desc_lower = description.lower()
    # Check directional phrases first (before matching exact phase names,
    # since "after full moon" would otherwise match "full moon").
    if "after new" in desc_lower or "before first" in desc_lower:
        return "Waxing Crescent"
    if "after first" in desc_lower or "before full" in desc_lower:
        return "Waxing Gibbous"
    if "after full" in desc_lower or "before last" in desc_lower:
        return "Waning Gibbous"
    if "after last" in desc_lower or "before new" in desc_lower:
        return "Waning Crescent"
    # Exact phase names
    if "new moon" in desc_lower:
        return "New"
    if "full moon" in desc_lower:
        return "Full"
    if "first quarter" in desc_lower:
        return "1st Quarter"
    if "last quarter" in desc_lower or "third quarter" in desc_lower:
        return "3rd Quarter"
    # Fallback: pure astronomical calculation (offline.py)
    from docking.applets.moon.offline import phase_from_illumination

    return phase_from_illumination(illumination=illumination)
