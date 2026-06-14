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

"""Pure logic for the bookmarks applet -- no GTK dependency."""

from __future__ import annotations

from typing import NamedTuple


class Bookmark(NamedTuple):
    name: str
    url: str


MAX_LABEL_LEN = 30


def truncate_label(text: str) -> str:
    """Truncate *text* with ellipsis if longer than MAX_LABEL_LEN."""
    if len(text) <= MAX_LABEL_LEN:
        return text
    return text[: MAX_LABEL_LEN - 1] + "\u2026"


def bookmarks_from_prefs(prefs: dict | None) -> list[Bookmark]:
    """Deserialize bookmarks list from an applet prefs dict."""
    if not prefs:
        return []
    raw = prefs.get("bookmarks", [])
    if not isinstance(raw, list):
        return []
    results: list[Bookmark] = []
    for entry in raw:
        if isinstance(entry, dict) and "name" in entry and "url" in entry:
            results.append(Bookmark(name=str(entry["name"]), url=str(entry["url"])))
    return results


def prefs_from_bookmarks(bookmarks: list[Bookmark]) -> dict:
    """Serialize bookmarks list into an applet prefs dict."""
    return {
        "bookmarks": [{"name": b.name, "url": b.url} for b in bookmarks],
    }


def tooltip_text(bookmarks: list[Bookmark]) -> str:
    """Build tooltip string from bookmarks list."""
    count = len(bookmarks)
    if count == 0:
        return "No bookmarks"
    return f"{count} bookmarks"
