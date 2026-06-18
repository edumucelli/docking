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

"""Recent-document-to-app association for per-app jumplist menus.

Reads the freedesktop.org recent-files store (via Gtk.RecentManager) and
associates each file with dock apps using a three-tier matching strategy
prioritised by precision:

1. ``has_application()`` with the app's display name — the file was
   *actually* opened with this app.  Most precise, zero false positives.
2. MIME-type match against the app's ``get_supported_types()`` — the app
   *could* open this file.  Capped at 5 items to limit noise.
3. System default-handler check — this app is the registered default for
   the file type.  Rarely hit, catches edge cases.

Tier 1 results are shown exclusively when they exist; Tiers 2 and 3 are
only used when Tier 1 is empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk

from docking.log import get_logger

if TYPE_CHECKING:
    from docking.platform.launcher import Launcher

log = get_logger(name="recent_docs")

# Tier-2 MIME fallback cap to prevent noisy results when every image app
# registers for image/png and similar broad types.
_MIME_FALLBACK_CAP = 5


@dataclass(frozen=True)
class RecentDoc:
    """One recent document associated with a dock app."""

    uri: str
    name: str  # short filename for display
    mime_type: str
    modified: int  # Unix timestamp


def recent_docs_for_app(
    desktop_id: str,
    launcher: Launcher,
    limit: int = 10,
) -> list[RecentDoc]:
    """Return recent documents associated with *desktop_id*, most-recent first.

    Args:
        desktop_id: The app's desktop-file ID (e.g. ``firefox.desktop``).
        launcher: Resolver for desktop metadata and display names.
        limit: Maximum number of documents to return (per-app cap).
    """
    if not desktop_id:
        return []

    try:
        app_info = Gio.DesktopAppInfo.new(desktop_id)
    except TypeError:
        return []
    if app_info is None:
        return []

    display_name = app_info.get_display_name()
    supported = app_info.get_supported_types()

    rm = Gtk.RecentManager.get_default()
    all_items = rm.get_items()
    if not all_items:
        return []

    # get_items() order is undefined — sort by modification time descending.
    sorted_items = sorted(all_items, key=lambda i: i.get_modified(), reverse=True)

    # ── Tier 1: files actually opened with this app ──
    tier1: list[RecentDoc] = []
    # ── Tier 2: files this app *could* open (MIME match) ──
    tier2: list[RecentDoc] = []
    # ── Tier 3: system default handler ──
    tier3: list[RecentDoc] = []

    for item in sorted_items:
        if not item.is_local():
            continue
        if not item.exists():
            continue
        mime = item.get_mime_type()
        if not mime:
            continue

        # Tier 1: has_application — the ground truth
        if item.has_application(display_name):
            tier1.append(_doc_from_item(item, mime))
            if len(tier1) >= limit:
                return tier1
            continue

        # Tier 2: MIME-type — noisy, only when Tier 1 is empty
        if supported and mime in supported and len(tier2) < _MIME_FALLBACK_CAP:
            tier2.append(_doc_from_item(item, mime))
            continue

        # Tier 3: default handler — edge cases
        if not supported:
            default = Gio.AppInfo.get_default_for_type(mime, False)
            if default and default.get_id() == desktop_id:
                tier3.append(_doc_from_item(item, mime))

    # Tier 1 results are shown exclusively — no MIME noise mixed in.
    if tier1:
        return tier1
    return (tier2 + tier3)[:limit]


def _doc_from_item(item, mime: str) -> RecentDoc:
    return RecentDoc(
        uri=item.get_uri(),
        name=item.get_short_name(),
        mime_type=mime,
        modified=item.get_modified(),
    )
