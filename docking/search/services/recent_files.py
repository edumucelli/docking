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

"""Immutable ``Gtk.RecentManager`` snapshots for Global Search.

All GTK and Gio calls are synchronous and must be made on the GTK main thread.
Published records contain only frozen Python values and may safely outlive a
manager signal or catalog lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from docking.log import get_logger, with_context

DEFAULT_MAX_ENTRIES = 15

log = with_context(get_logger(name="recent_files"))

CatalogListener = Callable[[], None]
RecentManagerFactory = Callable[[], object]
UriLauncher = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class RecentFileSnapshot:
    """Plain metadata for one existing recent file."""

    name: str
    uri: str
    modified: int
    mime_type: str = ""

    @property
    def display_name(self) -> str:
        return self.name


class RecentFilesCatalog:
    """Main-thread recent-file listing and change watcher."""

    def __init__(
        self,
        *,
        manager_factory: RecentManagerFactory | None = None,
        uri_launcher: UriLauncher | None = None,
        max_entries: int | None = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._manager_factory = manager_factory or Gtk.RecentManager.get_default
        self._uri_launcher = uri_launcher or _launch_default_for_uri
        self._max_entries = None if max_entries is None else max(0, int(max_entries))

        self._entries: tuple[RecentFileSnapshot, ...] = ()
        self._listeners: list[CatalogListener] = []
        self._generation = 0
        self._loaded = False
        self._started = False
        self._manager: object | None = None
        self._changed_handler: object | None = None

    @property
    def generation(self) -> int:
        """Monotonically increasing version of the published snapshot."""
        return self._generation

    @property
    def started(self) -> bool:
        return self._started

    @property
    def entries(self) -> tuple[RecentFileSnapshot, ...]:
        return self._entries

    def snapshot(self) -> tuple[RecentFileSnapshot, ...]:
        """Return the current immutable, most-recent-first sequence."""
        return self._entries

    def add_listener(self, listener: CatalogListener) -> Callable[[], None]:
        """Subscribe to changed snapshots and return an unsubscribe callback."""
        if listener not in self._listeners:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            self.remove_listener(listener)

        return unsubscribe

    def subscribe(self, listener: CatalogListener) -> Callable[[], None]:
        """Alias for :meth:`add_listener`."""
        return self.add_listener(listener)

    def remove_listener(self, listener: CatalogListener) -> None:
        """Remove a previously registered listener."""
        with suppress(ValueError):
            self._listeners.remove(listener)

    def start(self) -> None:
        """Connect to ``Gtk.RecentManager`` and populate the cache."""
        if self._started:
            return

        self._started = True
        manager = self._get_manager()
        if manager is not None:
            try:
                self._changed_handler = manager.connect(
                    "changed",
                    self._on_changed,
                )
            except Exception as exc:
                log.bind(action="monitor_recent_files").warning(
                    "Could not monitor recent files: %s",
                    exc,
                )
        self.refresh()

    def stop(self) -> None:
        """Disconnect the manager signal while retaining the last snapshot."""
        if not self._started:
            return

        self._started = False
        manager = self._manager
        handler = self._changed_handler
        self._manager = None
        self._changed_handler = None
        if manager is None or handler is None:
            return
        with suppress(AttributeError, TypeError, ValueError, GLib.Error):
            manager.disconnect(handler)

    def refresh(self) -> bool:
        """Synchronously rebuild the listing, returning whether it changed."""
        manager = self._get_manager()
        if manager is None:
            return False
        try:
            raw_items = tuple(manager.get_items())
        except Exception as exc:
            log.bind(action="list_recent_files").warning(
                "Could not list recent files: %s",
                exc,
            )
            return False

        candidates: list[RecentFileSnapshot] = []
        for item in raw_items:
            snapshot = _snapshot_from_item(item)
            if snapshot is not None:
                candidates.append(snapshot)
        candidates.sort(key=lambda entry: entry.modified, reverse=True)

        seen_uris: set[str] = set()
        entries: list[RecentFileSnapshot] = []
        for entry in candidates:
            if entry.uri in seen_uris:
                continue
            seen_uris.add(entry.uri)
            entries.append(entry)
            if self._max_entries is not None and len(entries) >= self._max_entries:
                break
        if self._max_entries == 0:
            entries = []

        snapshot = tuple(entries)
        changed = not self._loaded or snapshot != self._entries
        self._loaded = True
        if changed:
            self._entries = snapshot
            self._generation += 1
            self._notify_listeners()
        return changed

    def open(self, entry: RecentFileSnapshot | str) -> bool:
        """Open a recent snapshot or URI with its default application."""
        uri = entry.uri if isinstance(entry, RecentFileSnapshot) else entry
        return self.open_uri(uri)

    def open_uri(self, uri: str) -> bool:
        """Open ``uri`` with the desktop default application."""
        clean_uri = str(uri or "").strip()
        if not clean_uri:
            return False
        try:
            self._uri_launcher(clean_uri)
        except Exception as exc:
            log.bind(action="open_recent", uri=clean_uri).warning(
                "Failed to open recent file: %s",
                exc,
            )
            return False
        return True

    def clear(self) -> bool:
        """Purge recent files and immediately refresh the cache."""
        manager = self._get_manager()
        if manager is None:
            return False
        try:
            manager.purge_items()
        except Exception as exc:
            log.bind(action="clear_recent").warning(
                "Failed to clear recent files: %s",
                exc,
            )
            return False
        self.refresh()
        return True

    def clear_recent(self) -> bool:
        """Alias for :meth:`clear`."""
        return self.clear()

    def _get_manager(self) -> object | None:
        if self._manager is not None:
            return self._manager
        try:
            self._manager = self._manager_factory()
        except Exception as exc:
            log.bind(action="get_recent_manager").warning(
                "Could not obtain Gtk.RecentManager: %s",
                exc,
            )
            return None
        return self._manager

    def _on_changed(self, *_args: object) -> None:
        if self._started:
            self.refresh()

    def _notify_listeners(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception as exc:
                log.bind(action="notify_catalog_listener").warning(
                    "Recent files catalog listener failed: %s",
                    exc,
                )


def _launch_default_for_uri(uri: str) -> object:
    return Gio.AppInfo.launch_default_for_uri(uri, None)


def _snapshot_from_item(item: object) -> RecentFileSnapshot | None:
    if not bool(_safe_call(item, "exists", default=False)):
        return None
    uri = _clean_text(_safe_call(item, "get_uri"))
    if not uri:
        return None
    modified = _modified_timestamp(_safe_call(item, "get_modified"))
    name = _first_text(
        _safe_call(item, "get_display_name"),
        _safe_call(item, "get_short_name"),
        _name_from_uri(uri),
        uri,
    )
    mime_type = _clean_text(_safe_call(item, "get_mime_type"))
    return RecentFileSnapshot(
        name=name,
        uri=uri,
        modified=modified,
        mime_type=mime_type,
    )


def _modified_timestamp(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _name_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    return Path(unquote(parsed.path)).name


def _first_text(*values: Any) -> str:
    return next((text for value in values if (text := _clean_text(value))), "")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _safe_call(
    target: object,
    method_name: str,
    *args: object,
    default: Any = None,
) -> Any:
    method = getattr(target, method_name, None)
    if not callable(method):
        return default
    try:
        return method(*args)
    except Exception:
        return default


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "RecentFileSnapshot",
    "RecentFilesCatalog",
]
