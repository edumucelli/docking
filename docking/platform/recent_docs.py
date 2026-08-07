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
associates each file with dock apps using ``has_application()`` - the only
reliable signal for "this app actually opened this file".

MIME-type matching (``get_supported_types()``) was tested and rejected:
every image-handling app registers for ``image/png``, so Firefox, Chrome,
GIMP, and EOM would all show the same PNGs regardless of which app the
user actually used.  ``has_application()`` records the ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from docking.log import get_logger
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.types import ApplicationInfo

log = get_logger(name="recent_docs")


@dataclass(frozen=True)
class RecentDoc:
    """One recent document associated with a dock app."""

    uri: str
    name: str  # short filename for display
    mime_type: str
    modified: int  # Unix timestamp


def recent_docs_for_app(
    application: ApplicationInfo | str | None = None,
    launcher: object | None = None,
    limit: int = 10,
    *,
    registry: ApplicationRegistry | None = None,
    desktop_id: str = "",
) -> list[RecentDoc]:
    """Return recent documents associated with an app, most-recent first.

    Only returns files where ``has_application()`` confirms the app actually
    opened them.  No MIME-type fallback - that produces identical noise across
    all apps sharing the same broad type registrations.

    Args:
        application: Canonical application metadata, or a legacy desktop ID.
        launcher: Deprecated launcher position/keyword, ignored. An integer in
            this position retains the canonical second-positional limit shape.
        limit: Maximum number of documents to return (per-app cap).
        registry: Registry used to resolve a supplied desktop ID without Gio.
        desktop_id: Compatibility keyword for the former first argument.
    """
    effective_limit = (
        launcher
        if isinstance(launcher, int) and not isinstance(launcher, bool)
        else limit
    )

    selected: ApplicationInfo | str | None = application or desktop_id
    display_name = _application_display_name(selected, registry=registry)
    if not display_name:
        return []

    rm = Gtk.RecentManager.get_default()
    all_items = rm.get_items()
    if not all_items:
        return []

    # get_items() order is undefined - sort by modification time descending.
    sorted_items = sorted(all_items, key=lambda i: i.get_modified(), reverse=True)

    docs: list[RecentDoc] = []
    for item in sorted_items:
        if not item.is_local():
            continue
        if not item.exists():
            continue
        mime = item.get_mime_type()
        if not mime:
            continue
        if not item.has_application(display_name):
            continue

        docs.append(_doc_from_item(item, mime))
        if len(docs) >= effective_limit:
            break

    return docs


def _application_display_name(
    application: ApplicationInfo | str | None,
    *,
    registry: ApplicationRegistry | None,
) -> str:
    if isinstance(application, ApplicationInfo):
        return application.name
    desktop_id = str(application or "").strip()
    if not desktop_id:
        return ""
    # Keep the public desktop-ID form working for legacy callers. New
    # composition passes canonical metadata (or a registry) so it cannot drift
    # to a second Gio generation between result creation and preview/refine.
    resolver = registry
    if resolver is None:
        resolver = ApplicationRegistry()
        resolver.refresh()
    resolved = resolver.resolve(
        desktop_id,
        log_failures=False,
    )
    if resolved is None:
        return ""
    return resolved.name


def _doc_from_item(item, mime: str) -> RecentDoc:
    return RecentDoc(
        uri=item.get_uri(),
        name=item.get_short_name(),
        mime_type=mime,
        modified=item.get_modified(),
    )
