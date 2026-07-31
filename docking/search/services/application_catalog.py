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

"""Cached, immutable desktop-application metadata for Global Search.

The catalog deliberately performs discovery and Gio monitoring synchronously.
Callers must create, start, stop, and refresh it on the GTK main thread. The
published records contain only frozen Python values, so search code can retain
or inspect a snapshot without retaining ``Gio.DesktopAppInfo`` instances.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.applets.apps import all_desktop_app_infos
from docking.log import get_logger, with_context
from docking.platform import desktop_entries

DEFAULT_DEBOUNCE_MS = 150

log = with_context(get_logger(name="application_catalog"))

CatalogListener = Callable[[], None]


@dataclass(frozen=True, slots=True)
class IconDescriptor:
    """A toolkit-independent application icon reference."""

    kind: str
    value: str


@dataclass(frozen=True, slots=True)
class DesktopActionSnapshot:
    """One named action exposed by a desktop entry."""

    action_id: str
    name: str


@dataclass(frozen=True, slots=True)
class ApplicationSnapshot:
    """Plain metadata for one launchable desktop application."""

    desktop_id: str
    name: str
    normalized_name: str
    categories: tuple[str, ...]
    icon: IconDescriptor
    description: str = ""
    keywords: tuple[str, ...] = ()
    actions: tuple[DesktopActionSnapshot, ...] = ()

    @property
    def icon_descriptor(self) -> IconDescriptor:
        """Return the icon under its explicit descriptor name."""
        return self.icon


class ApplicationCatalog:
    """Maintain a main-thread application cache keyed by desktop ID.

    Gio application and directory monitors can emit several signals for one
    filesystem change. The catalog coalesces those signals through a short
    timeout, rebuilds plain snapshots synchronously, and notifies listeners
    only when the published value changes. A generation identifies each
    distinct snapshot, while a lifecycle token prevents a queued timeout from
    an earlier start cycle from mutating a restarted or stopped catalog.
    """

    def __init__(self) -> None:
        """Initialize empty snapshots, monitor factories, and debounce state."""
        self._application_source = all_desktop_app_infos
        self._desktop_directories_source = desktop_entries.desktop_dirs
        self._app_monitor_factory = Gio.AppInfoMonitor.get
        self._directory_monitor_factory = _monitor_directory
        self._schedule_timeout = GLib.timeout_add
        self._cancel_timeout = GLib.source_remove
        self._debounce_ms = DEFAULT_DEBOUNCE_MS

        self._applications_by_id: dict[str, ApplicationSnapshot] = {}
        self._ordered_snapshot: tuple[ApplicationSnapshot, ...] = ()
        self._listeners: list[CatalogListener] = []
        self._generation = 0
        self._loaded = False
        self._started = False
        self._lifecycle_token = 0

        self._app_monitor: object | None = None
        self._app_monitor_handler: object | None = None
        self._directory_monitors: dict[Path, tuple[object, object | None]] = {}
        self._debounce_source_id: int | None = None

    @property
    def generation(self) -> int:
        """Monotonically increasing version of the published snapshot."""
        return self._generation

    @property
    def started(self) -> bool:
        """Return whether filesystem and application monitoring is active."""
        return self._started

    @property
    def applications(self) -> tuple[ApplicationSnapshot, ...]:
        """Return the current immutable, name-sorted application sequence."""
        return self._ordered_snapshot

    def snapshot(self) -> tuple[ApplicationSnapshot, ...]:
        """Return the current immutable application sequence."""
        return self._ordered_snapshot

    def get(self, desktop_id: str) -> ApplicationSnapshot | None:
        """Return a cached application by desktop ID."""
        return self._applications_by_id.get(desktop_id)

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
        """Start Gio monitors and populate the cache.

        Repeated calls are safe and do not add duplicate signal handlers.
        """
        if self._started:
            return

        self._started = True
        self._lifecycle_token += 1
        self._connect_app_monitor()
        self._sync_directory_monitors()
        self.refresh()

    def stop(self) -> None:
        """Stop monitoring while preserving the last immutable snapshot."""
        if not self._started:
            return

        self._started = False
        self._lifecycle_token += 1
        self._cancel_pending_refresh()
        self._disconnect_app_monitor()
        self._cancel_directory_monitors()

    def refresh(self) -> bool:
        """Synchronously rebuild and publish the cache if its value changed.

        Individual malformed desktop entries are skipped so one third-party
        file cannot make the entire application provider unavailable. Duplicate
        desktop IDs keep the first discovered snapshot, then final ordering is
        deterministic by normalized name and ID.
        """
        try:
            entries = tuple(self._application_source())
        except Exception as exc:
            log.bind(action="discover_applications").warning(
                "Failed to discover desktop applications: %s",
                exc,
            )
            return False

        applications_by_id: dict[str, ApplicationSnapshot] = {}
        for entry in entries:
            try:
                application = _snapshot_from_entry(entry)
            except Exception as exc:
                log.bind(action="snapshot_application").warning(
                    "Failed to read desktop application metadata: %s",
                    exc,
                )
                continue
            if application is None:
                continue
            applications_by_id.setdefault(application.desktop_id, application)

        ordered = tuple(
            sorted(
                applications_by_id.values(),
                key=lambda application: (
                    application.normalized_name,
                    application.desktop_id.casefold(),
                ),
            )
        )
        changed = (
            not self._loaded
            or applications_by_id != self._applications_by_id
            or ordered != self._ordered_snapshot
        )
        self._loaded = True
        if changed:
            self._applications_by_id = applications_by_id
            self._ordered_snapshot = ordered
            self._generation += 1
            self._notify_listeners()

        if self._started:
            self._sync_directory_monitors()
        return changed

    def _connect_app_monitor(self) -> None:
        try:
            monitor = self._app_monitor_factory()
            handler = monitor.connect("changed", self._on_catalog_changed)
        except Exception as exc:
            log.bind(action="monitor_app_info").warning(
                "Could not monitor Gio application changes: %s",
                exc,
            )
            return
        self._app_monitor = monitor
        self._app_monitor_handler = handler

    def _disconnect_app_monitor(self) -> None:
        monitor = self._app_monitor
        handler = self._app_monitor_handler
        self._app_monitor = None
        self._app_monitor_handler = None
        if monitor is None or handler is None:
            return
        with suppress(AttributeError, TypeError, ValueError, GLib.Error):
            monitor.disconnect(handler)

    def _sync_directory_monitors(self) -> None:
        try:
            wanted = {
                Path(path).expanduser() for path in self._desktop_directories_source()
            }
        except Exception as exc:
            log.bind(action="list_desktop_directories").warning(
                "Could not list desktop application directories: %s",
                exc,
            )
            return

        for path in tuple(self._directory_monitors):
            if path not in wanted:
                self._cancel_directory_monitor(path)

        for path in sorted(wanted, key=str):
            if path in self._directory_monitors:
                continue
            try:
                monitor = self._directory_monitor_factory(path)
                handler = monitor.connect("changed", self._on_catalog_changed)
            except Exception as exc:
                log.bind(
                    action="monitor_desktop_directory",
                    path=str(path),
                ).warning(
                    "Could not monitor desktop application directory: %s",
                    exc,
                )
                continue
            self._directory_monitors[path] = (monitor, handler)

    def _cancel_directory_monitor(self, path: Path) -> None:
        monitor, handler = self._directory_monitors.pop(path)
        if handler is not None:
            with suppress(AttributeError, TypeError, ValueError, GLib.Error):
                monitor.disconnect(handler)
        with suppress(AttributeError, TypeError, ValueError, GLib.Error):
            monitor.cancel()

    def _cancel_directory_monitors(self) -> None:
        for path in tuple(self._directory_monitors):
            self._cancel_directory_monitor(path)

    def _on_catalog_changed(self, *_args: object) -> None:
        self._queue_refresh()

    def _queue_refresh(self) -> None:
        """Coalesce monitor bursts into one lifecycle-checked refresh."""
        if not self._started or self._debounce_source_id is not None:
            return
        lifecycle_token = self._lifecycle_token

        def run_refresh() -> bool:
            self._debounce_source_id = None
            if not self._started or lifecycle_token != self._lifecycle_token:
                return False
            self.refresh()
            return False

        self._debounce_source_id = self._schedule_timeout(
            self._debounce_ms,
            run_refresh,
        )

    def _cancel_pending_refresh(self) -> None:
        source_id = self._debounce_source_id
        self._debounce_source_id = None
        if source_id is None:
            return
        with suppress(TypeError, ValueError, GLib.Error):
            self._cancel_timeout(source_id)

    def _notify_listeners(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception as exc:
                log.bind(action="notify_catalog_listener").warning(
                    "Application catalog listener failed: %s",
                    exc,
                )


def normalize_search_text(value: str) -> str:
    """Return stable Unicode- and case-normalized search text."""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.casefold().split())


def _monitor_directory(path: Path) -> Gio.FileMonitor:
    file = Gio.File.new_for_path(str(path))
    return file.monitor_directory(Gio.FileMonitorFlags.NONE, None)


def _snapshot_from_entry(entry: object) -> ApplicationSnapshot | None:
    desktop_id = _first_text(
        _safe_call(entry, "get_id"),
        getattr(entry, "desktop_id", ""),
    )
    if not desktop_id:
        return None

    app_info = getattr(entry, "app_info", None)
    metadata_source = app_info if app_info is not None else entry
    name = _first_text(
        _safe_call(entry, "get_display_name"),
        getattr(entry, "name", ""),
        desktop_id.removesuffix(desktop_entries.DESKTOP_SUFFIX),
        desktop_id,
    )
    categories = _normalise_values(
        _first_value(
            getattr(entry, "categories", None),
            _safe_call(entry, "get_categories"),
        )
    )
    icon = _icon_from_entry(entry=entry, app_info=metadata_source)

    desktop_file = _desktop_file_for(
        desktop_id=desktop_id,
        app_info=metadata_source,
    )
    description = _clean_text(_safe_call(metadata_source, "get_description"))
    keywords = _normalise_values(_safe_call(metadata_source, "get_keywords"))
    actions = _actions_from(app_info=metadata_source)

    try:
        key_file = (
            desktop_entries.load_desktop_key_file(desktop_file)
            if desktop_file is not None
            else None
        )
    except Exception:
        key_file = None
    if key_file is not None:
        if not description:
            description = _first_text(
                desktop_entries.desktop_entry_locale_string(key_file, "Comment"),
                desktop_entries.desktop_entry_locale_string(key_file, "GenericName"),
            )
        if not keywords:
            keywords = _normalise_values(
                desktop_entries.desktop_entry_locale_string(
                    key_file,
                    "Keywords",
                )
            )

    if desktop_file is not None:
        actions = _merge_actions(
            actions,
            _desktop_file_actions(desktop_file),
        )

    return ApplicationSnapshot(
        desktop_id=desktop_id,
        name=name,
        normalized_name=normalize_search_text(name),
        categories=categories,
        icon=icon,
        description=description,
        keywords=keywords,
        actions=actions,
    )


def _desktop_file_for(*, desktop_id: str, app_info: object | None) -> Path | None:
    filename = _clean_text(_safe_call(app_info, "get_filename"))
    if filename:
        return Path(filename).expanduser()
    try:
        return desktop_entries.find_desktop_file(desktop_id)
    except Exception:
        return None


def _actions_from(*, app_info: object | None) -> tuple[DesktopActionSnapshot, ...]:
    action_ids = _safe_call(app_info, "list_actions")
    if not action_ids:
        return ()

    actions: list[DesktopActionSnapshot] = []
    for action_id_value in action_ids:
        action_id = _clean_text(action_id_value)
        if not action_id:
            continue
        name = _clean_text(_safe_call(app_info, "get_action_name", action_id))
        if name:
            actions.append(DesktopActionSnapshot(action_id=action_id, name=name))
    return tuple(actions)


def _desktop_file_actions(path: Path) -> tuple[DesktopActionSnapshot, ...]:
    try:
        actions = desktop_entries.desktop_file_actions(path)
    except Exception:
        return ()
    return tuple(
        DesktopActionSnapshot(
            action_id=_clean_text(action.action_id),
            name=_clean_text(action.display_name),
        )
        for action in actions
        if _clean_text(action.action_id) and _clean_text(action.display_name)
    )


def _merge_actions(
    *groups: tuple[DesktopActionSnapshot, ...],
) -> tuple[DesktopActionSnapshot, ...]:
    merged: dict[str, DesktopActionSnapshot] = {}
    for actions in groups:
        for action in actions:
            merged.setdefault(action.action_id, action)
    return tuple(merged.values())


def _icon_from_entry(*, entry: object, app_info: object | None) -> IconDescriptor:
    value = _first_text(getattr(entry, "icon_name", ""))
    if not value:
        icon = _safe_call(app_info, "get_icon")
        value = _clean_text(_safe_call(icon, "to_string"))
    if not value:
        return IconDescriptor(kind="none", value="")
    if value.startswith("file:") or Path(value).is_absolute():
        return IconDescriptor(kind="file", value=value)
    if "://" in value:
        return IconDescriptor(kind="serialized", value=value)
    return IconDescriptor(kind="themed", value=value)


def _normalise_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_values = value.split(";")
    else:
        try:
            raw_values = list(value)
        except TypeError:
            raw_values = [value]

    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        clean = _clean_text(raw_value)
        key = normalize_search_text(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return tuple(result)


def _first_value(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _first_text(*values: Any) -> str:
    return next((text for value in values if (text := _clean_text(value))), "")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _safe_call(target: object | None, method_name: str, *args: object) -> Any:
    if target is None:
        return None
    method = getattr(target, method_name, None)
    if not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None


__all__ = [
    "ApplicationCatalog",
    "ApplicationSnapshot",
    "DesktopActionSnapshot",
    "IconDescriptor",
    "normalize_search_text",
]
