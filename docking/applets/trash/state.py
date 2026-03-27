"""State helpers for Trash applet."""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.applets.trash import meta
from docking.log import get_logger, with_context

log = with_context(get_logger(name="trash"), applet_id=meta.id)


def _count_trash_items() -> int:
    """Count top-level items in trash:/// via Gio enumerator."""
    trash = Gio.File.new_for_uri("trash:///")
    try:
        enumerator = trash.enumerate_children(
            Gio.FILE_ATTRIBUTE_STANDARD_NAME, Gio.FileQueryInfoFlags.NONE, None
        )
    except GLib.Error as exc:
        log.bind(action="count_items").debug("Could not enumerate trash items: %s", exc)
        return 0

    count = 0
    while enumerator.next_file(None) is not None:
        count += 1
    enumerator.close(None)
    return count
