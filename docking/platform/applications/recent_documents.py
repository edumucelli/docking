"""Associate recent local documents with canonical applications."""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .types import ApplicationInfo


@dataclass(frozen=True, slots=True)
class RecentDocument:
    """One recent document associated with an application."""

    uri: str
    name: str
    mime_type: str
    modified: int


def recent_documents_for(
    application: ApplicationInfo,
    *,
    limit: int,
) -> tuple[RecentDocument, ...]:
    """Return the application's recent local documents, newest first."""
    if limit <= 0 or not application.name:
        return ()

    items = Gtk.RecentManager.get_default().get_items()
    if not items:
        return ()

    documents: list[RecentDocument] = []
    for item in sorted(
        items,
        key=lambda candidate: candidate.get_modified(),
        reverse=True,
    ):
        if not item.is_local() or not item.exists():
            continue
        mime_type = item.get_mime_type()
        if not mime_type or not item.has_application(application.name):
            continue
        documents.append(
            RecentDocument(
                uri=item.get_uri(),
                name=item.get_short_name(),
                mime_type=mime_type,
                modified=item.get_modified(),
            )
        )
        if len(documents) >= limit:
            break
    return tuple(documents)


__all__ = ["RecentDocument", "recent_documents_for"]
