"""GTK lifecycle glue for the APOD applet."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from docking.applets.apod import meta
from docking.applets.apod.api import ApodError, fetch_today
from docking.applets.apod.render import render_icon
from docking.applets.apod.state import (
    REFRESH_CHECK_INTERVAL_S,
    RETRY_ON_ERROR_S,
    ApodResult,
    build_tooltip,
    prefs_from_mapping,
    prefs_payload,
)
from docking.applets.base import Applet
from docking.applets.freshness import cadence_label
from docking.applets.live_state import (
    live_state_error,
    live_state_label,
    resolve_live_status,
)
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="apod"), applet_id=meta.id)

STARTUP_FETCH_DELAY_S = 2


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class ApodApplet(Applet):
    """Daily thumbnail of NASA's Astronomy Picture of the Day."""

    id = meta.id
    name = _("Astronomy Picture of the Day")
    icon_name = "image-x-generic"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._refresh_timer_id: int = 0
        self._retry_timer_id: int = 0
        self._startup_fetch_timer_id: int = 0
        self._fetch_request_id: int = 0
        self._loading = False
        self._error: str | None = None
        self._worker = BackgroundWorker(logger=log)

        prefs = prefs_from_mapping(
            config.applet_prefs.get(meta.id, {}) if config else None
        )
        self._result: ApodResult | None = prefs.last_result

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        cached = self._result.cached_path if self._result else ""
        return render_icon(size=size, cached_path=cached, warning=bool(self._error))

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            result=self._result,
            error=self._error,
            loading=self._loading,
            cadence_seconds=REFRESH_CHECK_INTERVAL_S,
        )

    def on_clicked(self) -> None:
        self._open_page()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = []
        if self._result is not None:
            status.append(
                disabled_menu_item(
                    _("{date}: {title}").format(
                        date=self._result.date,
                        title=self._result.title or _("Untitled"),
                    ),
                    gtk=Gtk,
                )
            )
            status.append(
                disabled_menu_item(
                    cadence_label(
                        seconds=REFRESH_CHECK_INTERVAL_S,
                        verb=_("Checks"),
                    ),
                    gtk=Gtk,
                )
            )
        state_status = self._live_status()
        state_label = live_state_label(state_status)
        if state_label:
            status.append(disabled_menu_item(state_label, gtk=Gtk))
        error = live_state_error(status=state_status, error=self._error)
        if error:
            status.append(
                disabled_menu_item(_("Error: {msg}").format(msg=error), gtk=Gtk)
            )

        open_item = Gtk.MenuItem(label=_("Open on apod.nasa.gov"))
        open_item.connect("activate", lambda _w: self._open_page())
        primary = [open_item]

        if self._result is not None and self._result.explanation:
            copy_item = Gtk.MenuItem(label=_("Copy Explanation"))
            copy_item.connect("activate", lambda _w: self._copy_explanation())
            primary.append(copy_item)

        refresh_item = Gtk.MenuItem(label=_("Refresh Now"))
        refresh_item.connect("activate", lambda _w: self._fetch_async())

        return menu_sections(
            status=status,
            primary=primary,
            refresh=[refresh_item],
            gtk=Gtk,
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._refresh_timer_id = GLib.timeout_add_seconds(
            REFRESH_CHECK_INTERVAL_S, self._tick
        )
        if self._needs_fetch():
            self._startup_fetch_timer_id = GLib.timeout_add_seconds(
                STARTUP_FETCH_DELAY_S, self._run_startup_fetch
            )

    def stop(self) -> None:
        for attr in ("_refresh_timer_id", "_retry_timer_id", "_startup_fetch_timer_id"):
            tid = getattr(self, attr, 0)
            if tid:
                GLib.source_remove(tid)
                setattr(self, attr, 0)
        super().stop()

    def _needs_fetch(self) -> bool:
        if self._result is None:
            return True
        return self._result.date != _today_iso()

    def _tick(self) -> bool:
        if self._needs_fetch():
            self._fetch_async()
        return True

    def _run_startup_fetch(self) -> bool:
        self._startup_fetch_timer_id = 0
        self._fetch_async()
        return False

    def _fetch_async(self) -> None:
        if self._startup_fetch_timer_id:
            GLib.source_remove(self._startup_fetch_timer_id)
            self._startup_fetch_timer_id = 0
        self._fetch_request_id += 1
        request_id = self._fetch_request_id
        self._loading = True
        self._error = None
        self.present()

        self._worker.run(
            name="apod-fetch",
            fn=fetch_today,
            on_result=lambda r: self._on_fetch_result(request_id=request_id, result=r),
            on_error=lambda exc: self._on_fetch_error(request_id=request_id, exc=exc),
        )

    def _on_fetch_result(
        self,
        *,
        request_id: int,
        result: ApodResult | ApodError,
    ) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        if isinstance(result, ApodError):
            self._error = result.message
            self._schedule_retry()
        else:
            self._result = result
            self._error = None
            self._save_prefs()
        self.present()
        return False

    def _on_fetch_error(self, *, request_id: int, exc: Exception) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        self._error = str(exc) or exc.__class__.__name__
        log.bind(action="fetch_error").debug("APOD fetch crashed: %s", exc)
        self._schedule_retry()
        self.present()
        return False

    def _schedule_retry(self) -> None:
        if self._retry_timer_id:
            return
        self._retry_timer_id = GLib.timeout_add_seconds(
            RETRY_ON_ERROR_S, self._run_retry
        )

    def _run_retry(self) -> bool:
        self._retry_timer_id = 0
        if self._needs_fetch():
            self._fetch_async()
        return False

    def _open_page(self) -> None:
        url = self._result.page_url if self._result else "https://apod.nasa.gov/"
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error as exc:
            log.bind(action="open_url").warning("Failed to open URL: %s", exc)

    def _copy_explanation(self) -> None:
        if self._result is None or not self._result.explanation:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(self._result.explanation, -1)

    def _save_prefs(self) -> None:
        self.save_prefs(prefs=prefs_payload(result=self._result))

    def _live_status(self):
        return resolve_live_status(
            has_data=self._result is not None,
            loading=self._loading,
            error=self._error,
        )
