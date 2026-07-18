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

"""GTK lifecycle glue for certwatch applet."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.certwatch import meta
from docking.applets.certwatch.api import fetch_cert
from docking.applets.certwatch.render import render_icon
from docking.applets.certwatch.state import (
    CertInfo,
    CertStatus,
    DomainPref,
    build_tooltip,
    format_host,
    icon_label,
    parse_host_port,
    prefs_from_mapping,
    prefs_payload,
    status_for_cert,
    tooltip_line,
    worst_status,
)
from docking.applets.freshness import cadence_label
from docking.applets.live_state import (
    live_state_error,
    live_state_label,
    resolve_live_status,
)
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.popup import add_cancel_ok_buttons, prepare_dialog_content
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="certwatch"), applet_id=meta.id)

REFRESH_INTERVAL_S = 3600  # 1 hour
RETRY_ON_ERROR_S = 300  # 5 minutes: recover from transient handshake failures
STARTUP_FETCH_DELAY_S = 2

ADD_DIALOG_WIDTH_PX = 350
DIALOG_CONTENT_SPACING_PX = 8
DIALOG_HORIZONTAL_MARGIN_PX = 12


class CertwatchApplet(Applet):
    """Monitor TLS cert expiry across a list of domains."""

    id = meta.id
    name = _("Cert Watch")
    icon_name = "application-certificate"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._startup_fetch_timer_id: int = 0
        self._retry_timer_id: int = 0
        self._fetch_request_id: int = 0
        self._last_updated: dt.datetime | None = None
        self._loading = False
        self._fetch_error = ""
        self._worker = BackgroundWorker(logger=log)

        prefs = prefs_from_mapping(
            config.applet_prefs.get(meta.id, {}) if config else None
        )
        self._domains: list[DomainPref] = list(prefs.domains)
        self._certs: dict[tuple[str, int], CertInfo] = {}

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        certs = self._current_certs()
        if self._domains and self._fetch_error and not certs:
            status = CertStatus.ERROR
            label = "!"
        else:
            status = worst_status(certs) if self._domains else CertStatus.UNKNOWN
            label = icon_label(certs) if self._domains else ""
        return render_icon(size=size, status=status, label=label)

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            domains=self._domains,
            certs=self._current_certs(),
            loading=self._loading,
            error=self._fetch_error,
            updated_at=self._last_updated,
            cadence_seconds=REFRESH_INTERVAL_S if self._domains else None,
        )

    def on_clicked(self) -> None:
        self._show_add_dialog()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = []

        if self._domains:
            for pref in self._domains:
                cert = self._certs.get((pref.host, pref.port))
                label = (
                    tooltip_line(cert=cert)
                    if cert is not None
                    else _("{host}: loading...").format(host=format_host(pref))
                )
                status.append(disabled_menu_item(label, gtk=Gtk))
            state_status = self._live_status()
            state_label = live_state_label(state_status)
            if state_label:
                status.append(disabled_menu_item(state_label, gtk=Gtk))
            error = live_state_error(status=state_status, error=self._fetch_error)
            if error:
                status.append(
                    disabled_menu_item(
                        _("Error: {msg}").format(msg=error),
                        gtk=Gtk,
                    )
                )
            status.append(
                disabled_menu_item(
                    cadence_label(seconds=REFRESH_INTERVAL_S, verb=_("Checks")),
                    gtk=Gtk,
                )
            )

        add_item = Gtk.MenuItem(label=_("Add Domain..."))
        add_item.connect("activate", lambda _w: self._show_add_dialog())

        refresh: list[Gtk.MenuItem] = []
        destructive: list[Gtk.MenuItem] = []
        if self._domains:
            refresh_item = Gtk.MenuItem(label=_("Refresh Now"))
            refresh_item.connect("activate", lambda _w: self._fetch_all())
            refresh.append(refresh_item)

            remove_menu = Gtk.Menu()
            for pref in self._domains:
                entry = Gtk.MenuItem(label=format_host(pref))
                entry.connect(
                    "activate",
                    lambda _w, p=pref: self._remove_domain(pref=p),
                )
                remove_menu.append(entry)
            remove_root = Gtk.MenuItem(label=_("Remove"))
            remove_root.set_submenu(remove_menu)
            destructive.append(remove_root)

        return menu_sections(
            status=status,
            refresh=refresh,
            manage=[add_item],
            destructive=destructive,
            gtk=Gtk,
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(REFRESH_INTERVAL_S, self._tick)
        if self._domains:
            self._startup_fetch_timer_id = GLib.timeout_add_seconds(
                STARTUP_FETCH_DELAY_S,
                self._run_startup_fetch,
            )

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._startup_fetch_timer_id:
            GLib.source_remove(self._startup_fetch_timer_id)
            self._startup_fetch_timer_id = 0
        if self._retry_timer_id:
            GLib.source_remove(self._retry_timer_id)
            self._retry_timer_id = 0
        super().stop()

    def _current_certs(self) -> list[CertInfo]:
        """Certs in the same order as configured domains."""
        return [
            self._certs[(d.host, d.port)]
            for d in self._domains
            if (d.host, d.port) in self._certs
        ]

    def _tick(self) -> bool:
        self._fetch_all()
        return True

    def _run_startup_fetch(self) -> bool:
        self._startup_fetch_timer_id = 0
        self._fetch_all()
        return False

    def _fetch_all(self) -> None:
        if not self._domains:
            return
        if self._startup_fetch_timer_id:
            GLib.source_remove(self._startup_fetch_timer_id)
            self._startup_fetch_timer_id = 0

        self._fetch_request_id += 1
        request_id = self._fetch_request_id
        targets = tuple(self._domains)
        self._loading = not self._current_certs()
        self._fetch_error = ""
        self.present()

        def fetch() -> list[CertInfo]:
            return [fetch_cert(host=d.host, port=d.port) for d in targets]

        self._worker.run(
            name="certwatch-fetch",
            fn=fetch,
            on_result=lambda result: self._on_fetch_result(
                request_id=request_id, certs=result
            ),
            on_error=lambda exc: self._on_fetch_error(request_id=request_id, exc=exc),
        )

    def _on_fetch_result(self, *, request_id: int, certs: list[CertInfo]) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        self._fetch_error = ""
        for cert in certs:
            self._certs[(cert.host, cert.port)] = cert
        self._last_updated = dt.datetime.now(dt.timezone.utc)
        self._prune_stale_certs()
        self._log_critical(certs=certs)
        self._schedule_retry_if_any_error(certs=certs)
        self.present()
        return False

    def _on_fetch_error(self, *, request_id: int, exc: Exception) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        self._fetch_error = str(exc) or exc.__class__.__name__
        log.bind(action="fetch_error").debug("Cert fetch failed: %s", exc)
        if not self._retry_timer_id:
            self._retry_timer_id = GLib.timeout_add_seconds(
                RETRY_ON_ERROR_S,
                self._run_retry,
            )
        self.present()
        return False

    def _schedule_retry_if_any_error(self, *, certs: list[CertInfo]) -> None:
        """Retry sooner when any cert errored, so transient faults self-heal."""
        if not any(c.error for c in certs):
            return
        if self._retry_timer_id:
            return
        self._retry_timer_id = GLib.timeout_add_seconds(
            RETRY_ON_ERROR_S,
            self._run_retry,
        )

    def _run_retry(self) -> bool:
        self._retry_timer_id = 0
        self._fetch_all()
        return False

    def _log_critical(self, *, certs: list[CertInfo]) -> None:
        for cert in certs:
            status = status_for_cert(cert=cert)
            if status in (CertStatus.CRITICAL, CertStatus.EXPIRED):
                log.bind(host=cert.host, status=status.value).warning(
                    "TLS certificate is near or past expiry",
                )

    def _prune_stale_certs(self) -> None:
        keys = {(d.host, d.port) for d in self._domains}
        for key in list(self._certs):
            if key not in keys:
                del self._certs[key]

    def _show_add_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Add Domain"),
            modal=True,
            destroy_with_parent=True,
        )
        self.register_popup_surface(dialog)
        add_cancel_ok_buttons(dialog=dialog)
        box = prepare_dialog_content(
            dialog=dialog,
            width=ADD_DIALOG_WIDTH_PX,
            spacing=DIALOG_CONTENT_SPACING_PX,
            margin=DIALOG_HORIZONTAL_MARGIN_PX,
            default_response=Gtk.ResponseType.OK,
        )

        entry = Gtk.Entry()
        entry.set_placeholder_text(_("example.com or example.com:8443"))
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)

        dialog.show_all()
        entry.grab_focus()

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            pref = parse_host_port(entry.get_text())
            if pref is not None:
                self._add_domain(pref=pref)
        dialog.destroy()

    def _add_domain(self, *, pref: DomainPref) -> None:
        if any(d.host == pref.host and d.port == pref.port for d in self._domains):
            return
        self._domains.append(pref)
        self._save_prefs()
        self._fetch_all()
        self.present()

    def _remove_domain(self, *, pref: DomainPref) -> None:
        self._domains = [
            d
            for d in self._domains
            if not (d.host == pref.host and d.port == pref.port)
        ]
        self._certs.pop((pref.host, pref.port), None)
        self._save_prefs()
        self.present()

    def _save_prefs(self) -> None:
        self.save_prefs(prefs=prefs_payload(domains=self._domains))

    def _live_status(self):
        return resolve_live_status(
            has_data=bool(self._current_certs()),
            loading=self._loading,
            error=self._fetch_error,
            updated_at=self._last_updated,
            stale_after_seconds=REFRESH_INTERVAL_S * 2,
        )
