"""Unix source wiring, kept out of the neutral catalog request."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

import gi

from docking.platform.applications.backend import ApplicationCatalogBackend

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio

from ...backend import DiscoveryResult
from ...steam import SteamDiscoveryResult, steam_app_id_from_exec
from . import discovery, entries
from .monitor import UnixCatalogMonitor
from .steam import game_application


class UnixApplicationCatalogBackend(ApplicationCatalogBackend):
    game_application = staticmethod(game_application)

    def __init__(
        self,
        *,
        application_source: Callable[[], Iterable[object]] | None = None,
        desktop_directories_source: Callable[[], Iterable[Path]] | None = None,
        steam_source: Callable[[], SteamDiscoveryResult] | None = None,
    ) -> None:
        self._application_source = (
            discovery.default_gio_applications
            if application_source is None
            else application_source
        )
        self._desktop_directories_source = (
            desktop_directories_source or entries.desktop_dirs
        )
        self._steam_source = steam_source
        self._desktop_app_info_for_id = Gio.DesktopAppInfo.new
        self._desktop_app_info_from_filename = Gio.DesktopAppInfo.new_from_filename

    def discover(self, *, handle_epoch: int) -> DiscoveryResult:
        return discovery.discover(
            application_source=self._application_source,
            desktop_directories_source=self._desktop_directories_source,
            desktop_app_info_for_id=self._desktop_app_info_for_id,
            desktop_app_info_from_filename=self._desktop_app_info_from_filename,
            handle_epoch=handle_epoch,
        )

    def steam(self) -> SteamDiscoveryResult:
        return (
            self._steam_source()
            if self._steam_source is not None
            else SteamDiscoveryResult()
        )

    def create_monitor(self, changed: Callable[[], None]) -> UnixCatalogMonitor:
        return UnixCatalogMonitor(changed, self._desktop_directories_source)

    def hidden_steam_id(self, path: Path) -> str | None:
        facts = discovery.file_facts(path)
        return steam_app_id_from_exec(facts.exec_line) if facts is not None else None
