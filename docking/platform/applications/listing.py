"""Application listing helpers over the canonical registry.

These functions replace the duck-typed ``ApplicationEntry`` adapter
from ``applets/apps.py``.  They operate on ``ApplicationInfo`` records
from the process-wide ``ApplicationRegistry``.
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio

from docking.platform.applications.types import ApplicationInfo


def gicon_for(info: ApplicationInfo) -> Gio.Icon | None:
    """Return a Gio.Icon for *info* (replaces ``ApplicationEntry.get_icon``)."""
    if not info.icon_name:
        return None
    icon_path = Path(info.icon_name)
    if icon_path.is_absolute():
        return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))
    return Gio.ThemedIcon.new(info.icon_name)


def desktop_file_uri(info: ApplicationInfo) -> str | None:
    """Return the ``.desktop`` file URI used for drag-to-pin."""
    if info.desktop_file is not None:
        return info.desktop_file.expanduser().resolve().as_uri()
    from docking.platform import desktop_entries

    path = desktop_entries.find_desktop_file(info.desktop_id)
    if path is None:
        return None
    return path.expanduser().resolve().as_uri()


def launch_app(info: ApplicationInfo) -> None:
    """Launch *info* via the canonical launcher."""
    from docking.platform.launcher import launch

    if info.desktop_id:
        launch(desktop_id=info.desktop_id)
