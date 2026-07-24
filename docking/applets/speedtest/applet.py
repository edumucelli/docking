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

"""GTK lifecycle glue for speedtest applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

from docking.applets.base import Applet
from docking.applets.freshness import on_demand_label
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.speedtest import meta
from docking.applets.speedtest.api import SpeedtestError, run_librespeed
from docking.applets.speedtest.render import render_icon
from docking.applets.speedtest.state import (
    SpeedtestResult,
    build_tooltip,
    format_timestamp,
    prefs_from_mapping,
    prefs_payload,
)
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="speedtest"), applet_id=meta.id)


class SpeedtestApplet(Applet):
    """One-click LibreSpeed test with last result persisted."""

    id = meta.id
    name = _("Speedtest")
    icon_name = "network-wired"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._running: bool = False
        self._error: str | None = None
        self._run_request_id: int = 0
        self._worker = BackgroundWorker(logger=log)

        prefs = prefs_from_mapping(config.applet_prefs.get(meta.id, {}))
        self._result: SpeedtestResult | None = prefs.last_result

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        download = self._result.download_mbps if self._result else None
        label = _("...") if self._running else ""
        return render_icon(size=size, download_mbps=download, label=label)

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            result=self._result,
            running=self._running,
            error=self._error,
        )

    def on_clicked(self) -> None:
        self._start_test()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = []
        header = self._menu_header_label()
        if header:
            status.append(disabled_menu_item(header, gtk=Gtk))

        status.append(disabled_menu_item(on_demand_label(verb=_("Runs")), gtk=Gtk))

        run_item = Gtk.MenuItem(
            label=_("Running...") if self._running else _("Run Test")
        )
        run_item.set_sensitive(not self._running)
        run_item.connect("activate", lambda _w: self._start_test())
        primary = [run_item]

        if self._result is not None:
            copy_item = Gtk.MenuItem(label=_("Copy Last Result"))
            copy_item.connect("activate", lambda _w: self._copy_last_result())
            primary.append(copy_item)

        return menu_sections(status=status, primary=primary, gtk=Gtk)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)

    def _menu_header_label(self) -> str:
        if self._running:
            return _("Running...")
        if self._error:
            return _("Error: {msg}").format(msg=self._error)
        if self._result is None:
            return ""
        return _("Down {d:.1f} / Up {u:.1f} Mbps").format(
            d=self._result.download_mbps,
            u=self._result.upload_mbps,
        )

    def _start_test(self) -> None:
        if self._running:
            return
        self._error = None
        self._running = True
        self._run_request_id += 1
        request_id = self._run_request_id
        self.present()

        def fetch() -> SpeedtestResult | SpeedtestError:
            return run_librespeed()

        self._worker.run(
            name="speedtest-run",
            fn=fetch,
            on_result=lambda result: self._on_result(
                request_id=request_id, result=result
            ),
            on_error=lambda exc: self._on_error(request_id=request_id, exc=exc),
        )

    def _on_result(
        self,
        *,
        request_id: int,
        result: SpeedtestResult | SpeedtestError,
    ) -> bool:
        if request_id != self._run_request_id:
            return False
        self._running = False
        if isinstance(result, SpeedtestError):
            self._error = result.message
        else:
            self._result = result
            self._error = None
            self._save_prefs()
        self.present()
        return False

    def _on_error(self, *, request_id: int, exc: Exception) -> bool:
        if request_id != self._run_request_id:
            return False
        self._running = False
        self._error = str(exc) or exc.__class__.__name__
        log.bind(action="run_error").debug("Speedtest failed: %s", exc)
        self.present()
        return False

    def _copy_last_result(self) -> None:
        if self._result is None:
            return
        text = _(
            "Down: {d:.2f} Mbps, Up: {u:.2f} Mbps, Ping: {p:.1f} ms, "
            "Jitter: {j:.1f} ms ({when})"
        ).format(
            d=self._result.download_mbps,
            u=self._result.upload_mbps,
            p=self._result.ping_ms,
            j=self._result.jitter_ms,
            when=format_timestamp(self._result.timestamp),
        )
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)

    def _save_prefs(self) -> None:
        self.save_prefs(prefs=prefs_payload(result=self._result))
