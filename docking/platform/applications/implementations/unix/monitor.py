"""Gio catalog and source directory observation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import suppress
from pathlib import Path

import gi

from docking.platform.applications.backend import CatalogMonitor

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger, with_context

from . import discovery

log = with_context(get_logger(name="application_registry"))


def _monitor_directory(path: Path) -> object:
    file = Gio.File.new_for_path(str(path))
    return file.monitor_directory(Gio.FileMonitorFlags.NONE, None)


class UnixCatalogMonitor(CatalogMonitor):
    def __init__(
        self, changed: Callable[[], None], directories: Callable[[], Iterable[Path]]
    ) -> None:
        self._changed = changed
        self._desktop_directories_source = directories
        self._extra_directories: tuple[Path, ...] = ()
        self._app_monitor_factory = Gio.AppInfoMonitor.get
        self._directory_monitor_factory = _monitor_directory
        self._app_monitor: object | None = None
        self._app_monitor_handler: object | None = None
        self._directory_monitors: dict[Path, tuple[object, object | None]] = {}

    def start(self) -> None:
        self._connect_app_monitor()

    def sync(self, directories: tuple[Path, ...]) -> None:
        self._extra_directories = directories
        self._sync_directory_monitors()

    def stop(self) -> None:
        self._disconnect_app_monitor()
        self._cancel_directory_monitors()

    def _on_registry_changed(self, *_args: object) -> None:
        self._changed()

    def _connect_app_monitor(self) -> None:
        try:
            monitor = self._app_monitor_factory()
            handler = monitor.connect("changed", self._on_registry_changed)
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
            wanted = set(discovery.unique_paths(self._desktop_directories_source()))
            wanted.update(self._extra_directories)
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
                handler = monitor.connect(
                    "changed",
                    self._on_registry_changed,
                )
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
            with suppress(
                AttributeError,
                TypeError,
                ValueError,
                GLib.Error,
            ):
                monitor.disconnect(handler)
        with suppress(AttributeError, TypeError, ValueError, GLib.Error):
            monitor.cancel()

    def _cancel_directory_monitors(self) -> None:
        for path in tuple(self._directory_monitors):
            self._cancel_directory_monitor(path)
