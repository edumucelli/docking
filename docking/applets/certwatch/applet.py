"""GTK lifecycle glue for certwatch applet."""

from __future__ import annotations

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
DIALOG_VERTICAL_MARGIN_PX = 8


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
        status = worst_status(certs) if self._domains else CertStatus.UNKNOWN
        label = icon_label(certs) if self._domains else ""
        return render_icon(size=size, status=status, label=label)

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            domains=self._domains,
            certs=self._current_certs(),
        )

    def on_clicked(self) -> None:
        self._show_add_dialog()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        if self._domains:
            for pref in self._domains:
                cert = self._certs.get((pref.host, pref.port))
                label = (
                    tooltip_line(cert=cert)
                    if cert is not None
                    else _("{host}: loading...").format(host=format_host(pref))
                )
                row = Gtk.MenuItem(label=label)
                row.set_sensitive(False)
                items.append(row)
            items.append(Gtk.SeparatorMenuItem())

        add_item = Gtk.MenuItem(label=_("Add Domain..."))
        add_item.connect("activate", lambda _w: self._show_add_dialog())
        items.append(add_item)

        if self._domains:
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
            items.append(remove_root)

            refresh = Gtk.MenuItem(label=_("Refresh Now"))
            refresh.connect("activate", lambda _w: self._fetch_all())
            items.append(refresh)

        return items

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
        for cert in certs:
            self._certs[(cert.host, cert.port)] = cert
        self._prune_stale_certs()
        self._log_critical(certs=certs)
        self._schedule_retry_if_any_error(certs=certs)
        self.present()
        return False

    def _on_fetch_error(self, *, request_id: int, exc: Exception) -> bool:
        if request_id != self._fetch_request_id:
            return False
        log.bind(action="fetch_error").debug("Cert fetch failed: %s", exc)
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
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK,
            Gtk.ResponseType.OK,
        )
        dialog.set_default_size(ADD_DIALOG_WIDTH_PX, -1)
        dialog.set_position(Gtk.WindowPosition.MOUSE)

        box = dialog.get_content_area()
        box.set_spacing(DIALOG_CONTENT_SPACING_PX)
        box.set_margin_start(DIALOG_HORIZONTAL_MARGIN_PX)
        box.set_margin_end(DIALOG_HORIZONTAL_MARGIN_PX)
        box.set_margin_top(DIALOG_VERTICAL_MARGIN_PX)
        box.set_margin_bottom(DIALOG_VERTICAL_MARGIN_PX)

        entry = Gtk.Entry()
        entry.set_placeholder_text(_("example.com or example.com:8443"))
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)

        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.show_all()

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
