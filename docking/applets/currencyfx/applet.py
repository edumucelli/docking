"""GTK lifecycle glue for the Currency FX applet.

The applet is split so this file only handles the dock-facing behavior:
rendering invalidation, timers, menus, dialogs, scroll cycling, and preference
writes.  Data normalization, fetch parsing, and formatting live in
``state.py`` so they can be tested without GTK.

Feature map:

* click opens Add FX Pair, which appends to the user-managed pair list.
* scroll cycles only through added pairs, preserving add order.
* right click exposes refresh, swap, add, interval, pair switch, and remove.
* day charts use locally cached live current-rate samples.
* week and month charts use remote daily history plus the current rate.
* the latest chart point pulses while the icon has visible chart data.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.currencyfx import meta
from docking.applets.currencyfx.render import render_icon
from docking.applets.currencyfx.state import (
    REFRESH_INTERVAL_S,
    STARTUP_FETCH_DELAY_S,
    ChartInterval,
    FxPair,
    FxSnapshot,
    append_local_sample,
    build_tooltip,
    fetch_fx_snapshot,
    format_change,
    format_rate,
    local_sample_points,
    merge_currency_codes,
    normalize_pair,
    pair_key,
    prefs_from_mapping,
    prefs_payload,
)
from docking.applets.freshness import cadence_label
from docking.applets.live_state import (
    live_state_error,
    live_state_label,
    resolve_live_status,
)
from docking.applets.menu import disabled_menu_item, menu_sections, radio_submenu
from docking.applets.popup import (
    add_cancel_ok_buttons,
    entry_completion_combo,
    prepare_dialog_content,
)
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="currencyfx"), applet_id=meta.id)

DIALOG_WIDTH_PX = 260
DIALOG_SPACING_PX = 8
DIALOG_MARGIN_PX = 12
_PULSE_INTERVAL_MS = 60
_PULSE_PERIOD_MS = 1800


class CurrencyFxApplet(Applet):
    """Live FX pair applet with a sparkline icon.

    The icon always labels the active pair as base code on top and quote code
    on the bottom.  The chart points come from the selected interval, while the
    currency picker stays aligned with Unit Converter's currency ids.
    """

    id = meta.id
    name = _("Currency FX")
    icon_name = "emblem-synchronizing-symbolic"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        """Load preferences and render the first icon state.

        A day interval can render immediately from the local cache before the
        first network refresh.  Week/month wait for the startup fetch because
        their chart points are remote daily history.
        """
        self._timer_id: int = 0
        self._startup_fetch_timer_id: int = 0
        self._pulse_timer_id: int = 0
        self._pulse_phase: float = 0.0
        self._fetch_request_id: int = 0
        self._snapshot: FxSnapshot | None = None
        self._loading = False
        self._fetch_failed = False
        self._fetch_error = ""
        self._worker = BackgroundWorker(logger=log)

        raw_prefs = config.applet_prefs.get(meta.id, {}) if config else None
        prefs = prefs_from_mapping(raw_prefs)
        self._pairs = list(prefs.pairs)
        self._active_index = prefs.active_index
        self._chart_interval = prefs.chart_interval
        self._samples = dict(prefs.samples)
        self._available_codes = merge_currency_codes(self._pair_codes())
        if self._chart_interval == ChartInterval.DAY:
            self._snapshot = self._snapshot_from_local_samples()

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    @property
    def _active_pair(self) -> FxPair:
        return self._pairs[self._active_index]

    @property
    def _base(self) -> str:
        return self._active_pair.base

    @property
    def _quote(self) -> str:
        return self._active_pair.quote

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Render the current tray icon pixbuf."""
        phase = self._pulse_phase if self._has_chart_dot() else None
        return render_icon(
            size=size,
            snapshot=self._snapshot,
            base=self._base,
            quote=self._quote,
            fetch_failed=self._fetch_failed,
            pulse_phase=phase,
        )

    def refresh_tooltip(self) -> None:
        """Update tooltip text from the latest snapshot or loading state."""
        self.item.name = build_tooltip(
            base=self._base,
            quote=self._quote,
            snapshot=self._snapshot,
            loading=self._loading,
            fetch_failed=self._fetch_failed,
            error=self._fetch_error,
            chart_interval=self._chart_interval,
            cadence_seconds=REFRESH_INTERVAL_S,
        )

    def on_clicked(self) -> None:
        """Open Add FX Pair on primary click."""
        self._show_pair_dialog()

    def on_scroll(self, direction_up: bool) -> None:
        """Cycle through added FX pairs on scroll."""
        if len(self._pairs) <= 1:
            return
        step = -1 if direction_up else 1
        self._activate_pair_index((self._active_index + step) % len(self._pairs))

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Build the right-click menu for all Currency FX controls."""
        status: list[Gtk.MenuItem] = [disabled_menu_item(self._menu_header(), gtk=Gtk)]
        verb = _("Day samples") if self._chart_interval == ChartInterval.DAY else None
        status.append(
            disabled_menu_item(
                cadence_label(seconds=REFRESH_INTERVAL_S, verb=verb),
                gtk=Gtk,
            )
        )
        state_status = self._live_status()
        state_label = live_state_label(state_status)
        if state_label:
            status.append(disabled_menu_item(state_label, gtk=Gtk))
        error = live_state_error(status=state_status, error=self._fetch_error)
        if error:
            status.append(
                disabled_menu_item(_("Error: {msg}").format(msg=error), gtk=Gtk)
            )

        navigation: list[Gtk.MenuItem] = []
        swap = Gtk.MenuItem(label=_("Swap Pair"))
        swap.connect("activate", lambda _w: self._add_pair(self._quote, self._base))
        navigation.append(swap)

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _w: self._fetch_async())
        refresh_items = [refresh]

        display: list[Gtk.MenuItem] = [
            radio_submenu(
                label=_("Chart Interval"),
                choices=(
                    (_("Day"), ChartInterval.DAY),
                    (_("Week"), ChartInterval.WEEK),
                    (_("Month"), ChartInterval.MONTH),
                ),
                active_value=self._chart_interval,
                on_selected=lambda widget, value: self._on_interval_selected(
                    widget=widget,
                    interval=value,
                ),
                gtk=Gtk,
            )
        ]

        destructive: list[Gtk.MenuItem] = []
        if len(self._pairs) > 1:
            display.append(
                radio_submenu(
                    label=_("Added Pairs"),
                    choices=tuple(
                        (f"{pair.base}/{pair.quote}", index)
                        for index, pair in enumerate(self._pairs)
                    ),
                    active_value=self._active_index,
                    on_selected=lambda _widget, value: self._activate_pair_index(value),
                    gtk=Gtk,
                )
            )
            remove = Gtk.MenuItem(label=_("Remove Current Pair"))
            remove.connect(
                "activate",
                lambda _w: self._remove_active_pair(),
            )
            destructive.append(remove)

        choose = Gtk.MenuItem(label=_("Add Pair..."))
        choose.connect("activate", lambda _w: self._show_pair_dialog())

        return menu_sections(
            status=status,
            navigation=navigation,
            refresh=refresh_items,
            display=display,
            manage=[choose],
            destructive=destructive,
            gtk=Gtk,
        )

    def start(self, notify: Callable[[], None]) -> None:
        """Start periodic refresh and one delayed startup fetch."""
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(REFRESH_INTERVAL_S, self._tick)
        self._startup_fetch_timer_id = GLib.timeout_add_seconds(
            STARTUP_FETCH_DELAY_S,
            self._run_startup_fetch,
        )
        self._ensure_pulse_timer()

    def stop(self) -> None:
        """Stop timers before the dock unloads the applet."""
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._startup_fetch_timer_id:
            GLib.source_remove(self._startup_fetch_timer_id)
            self._startup_fetch_timer_id = 0
        if self._pulse_timer_id:
            GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = 0
        super().stop()

    def _tick(self) -> bool:
        """Refresh on the GLib interval timer."""
        self._fetch_async()
        return True

    def _run_startup_fetch(self) -> bool:
        """Run the first refresh after the dock has settled."""
        self._startup_fetch_timer_id = 0
        self._fetch_async()
        return False

    def _fetch_async(self) -> None:
        """Fetch the active pair without blocking the GTK main loop.

        ``request_id`` prevents slower, older requests from overwriting a newer
        pair or interval selection.
        """
        if self._startup_fetch_timer_id:
            GLib.source_remove(self._startup_fetch_timer_id)
            self._startup_fetch_timer_id = 0

        self._fetch_request_id += 1
        request_id = self._fetch_request_id
        base = self._base
        quote = self._quote
        chart_interval = self._chart_interval
        self._loading = True
        self._fetch_failed = False
        self._fetch_error = ""
        self.present()

        self._worker.run(
            name="currencyfx-fetch",
            fn=lambda: fetch_fx_snapshot(
                base=base,
                quote=quote,
                chart_interval=chart_interval,
            ),
            on_result=lambda result: self._on_fetch_result(
                request_id=request_id,
                snapshot=result[0],
                codes=result[1],
            ),
            on_error=lambda exc: self._on_fetch_error(request_id=request_id, exc=exc),
        )

    def _on_fetch_result(
        self,
        *,
        request_id: int,
        snapshot: FxSnapshot | None,
        codes: tuple[str, ...],
    ) -> bool:
        """Apply a completed fetch to applet state.

        Every successful fetch adds a current-rate local sample.  If the user
        is looking at the day chart, the remote snapshot is immediately rebuilt
        with those local samples because day does not use remote history.
        """
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        self._available_codes = merge_currency_codes((*codes, *self._pair_codes()))
        if snapshot is not None:
            self._samples = append_local_sample(
                samples=self._samples,
                base=snapshot.base,
                quote=snapshot.quote,
                rate=snapshot.rate,
                now=snapshot.fetched_at,
            )
            if self._chart_interval == ChartInterval.DAY:
                snapshot = self._snapshot_with_local_samples(snapshot=snapshot)
            self._save_prefs()
            self._snapshot = snapshot
            self._fetch_error = ""
        else:
            self._fetch_error = _("No rate data")
        self._fetch_failed = snapshot is None
        self._ensure_pulse_timer()
        self.present()
        return False

    def _on_fetch_error(self, *, request_id: int, exc: Exception) -> bool:
        """Mark the active request as failed and redraw the empty state."""
        if request_id != self._fetch_request_id:
            return False
        log.bind(action="fetch_error").debug("Currency FX fetch failed: %s", exc)
        self._loading = False
        self._fetch_failed = True
        self._fetch_error = str(exc) or exc.__class__.__name__
        self._ensure_pulse_timer()
        self.present()
        return False

    def _add_pair(self, base: str, quote: str) -> None:
        """Append a pair or activate it if already present."""
        if base == quote:
            return
        pair = normalize_pair(base=base, quote=quote)
        if pair in self._pairs:
            self._activate_pair_index(self._pairs.index(pair))
            return
        self._pairs.append(pair)
        self._active_index = len(self._pairs) - 1
        self._pair_changed()

    def _activate_pair_index(self, index: int) -> None:
        """Switch active pair by index from scroll or the Added Pairs menu."""
        if not self._pairs:
            return
        index = max(0, min(index, len(self._pairs) - 1))
        if index == self._active_index:
            return
        self._active_index = index
        self._pair_changed()

    def _remove_active_pair(self) -> None:
        """Remove the active pair and its local day-cache samples."""
        if len(self._pairs) <= 1:
            return
        removed = self._pairs[self._active_index]
        del self._pairs[self._active_index]
        self._samples.pop(pair_key(base=removed.base, quote=removed.quote), None)
        self._active_index = min(self._active_index, len(self._pairs) - 1)
        self._pair_changed()

    def _pair_changed(self) -> None:
        """Persist active pair changes and trigger a fresh fetch."""
        self._snapshot = (
            self._snapshot_from_local_samples()
            if self._chart_interval == ChartInterval.DAY
            else None
        )
        self._fetch_failed = False
        self._fetch_error = ""
        self._loading = False
        self._available_codes = merge_currency_codes(self._pair_codes())
        self._save_prefs()
        self._fetch_async()
        self._ensure_pulse_timer()
        self.present()

    def _save_prefs(self) -> None:
        """Persist the current pair list, interval, and day sample cache."""
        self.save_prefs(
            prefs=prefs_payload(
                pairs=tuple(self._pairs),
                active_index=self._active_index,
                chart_interval=self._chart_interval,
                samples=self._samples,
            )
        )

    def _menu_header(self) -> str:
        """Return menu heading with current rate and interval change."""
        pair = f"{self._base}/{self._quote}"
        if not self._snapshot:
            return pair
        return _("{pair}: {rate} ({change})").format(
            pair=pair,
            rate=format_rate(self._snapshot.rate),
            change=format_change(self._snapshot.points),
        )

    def _live_status(self):
        return resolve_live_status(
            has_data=self._snapshot is not None,
            loading=self._loading,
            error=self._fetch_error if self._fetch_failed else None,
            updated_at=self._snapshot.fetched_at if self._snapshot else None,
            stale_after_seconds=REFRESH_INTERVAL_S * 2,
        )

    def _on_interval_selected(
        self,
        *,
        widget: Gtk.RadioMenuItem,
        interval: ChartInterval,
    ) -> None:
        """Persist chart interval selection and refresh its data source."""
        if not widget.get_active():
            return
        if interval == self._chart_interval:
            return
        self._chart_interval = interval
        self._snapshot = (
            self._snapshot_from_local_samples()
            if interval == ChartInterval.DAY
            else None
        )
        self._fetch_failed = False
        self._fetch_error = ""
        self._loading = False
        self._save_prefs()
        self._fetch_async()
        self._ensure_pulse_timer()
        self.present()

    def _show_pair_dialog(self) -> None:
        """Show the Add FX Pair dialog using the currently known codes."""
        dialog = Gtk.Dialog(
            title=_("Add FX Pair"),
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        add_cancel_ok_buttons(dialog=dialog)
        box = prepare_dialog_content(
            dialog=dialog,
            width=DIALOG_WIDTH_PX,
            spacing=DIALOG_SPACING_PX,
            margin=DIALOG_MARGIN_PX,
            default_response=Gtk.ResponseType.OK,
        )

        base_combo = self._currency_combo(active=self._base)
        quote_combo = self._currency_combo(active=self._quote)
        box.pack_start(Gtk.Label(label=_("Base")), False, False, 0)
        box.pack_start(base_combo, False, False, 0)
        box.pack_start(Gtk.Label(label=_("Quote")), False, False, 0)
        box.pack_start(quote_combo, False, False, 0)

        dialog.show_all()
        base_combo.grab_focus()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            base = self._currency_combo_text(combo=base_combo, fallback=self._base)
            quote = self._currency_combo_text(combo=quote_combo, fallback=self._quote)
            self._add_pair(base, quote)
        dialog.destroy()

    def _currency_combo(self, *, active: str) -> Gtk.ComboBoxText:
        """Build a currency-code combo box with a selected active code."""
        combo = entry_completion_combo()
        active_index = 0
        for index, code in enumerate(self._available_codes):
            combo.append_text(code)
            if code == active:
                active_index = index
        combo.set_active(active_index)
        return combo

    def _currency_combo_text(self, *, combo: Gtk.ComboBoxText, fallback: str) -> str:
        """Return typed or selected currency code from an editable combo."""
        text = combo.get_child().get_text().strip()
        if text:
            return text
        return combo.get_active_text() or fallback

    def _pair_codes(self) -> tuple[str, ...]:
        """Return all codes currently referenced by added pairs."""
        return tuple(code for pair in self._pairs for code in (pair.base, pair.quote))

    def _has_chart_dot(self) -> bool:
        """Return whether the current icon has a latest chart point to pulse."""
        return bool(self._snapshot and self._snapshot.points)

    def _ensure_pulse_timer(self) -> None:
        """Run the endpoint pulse only while the chart dot is visible."""
        should_pulse = self._notify is not None and self._has_chart_dot()
        if should_pulse and not self._pulse_timer_id:
            self._pulse_timer_id = GLib.timeout_add(
                _PULSE_INTERVAL_MS,
                self._pulse_tick,
            )
        elif not should_pulse and self._pulse_timer_id:
            GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = 0
            self._pulse_phase = 0.0

    def _pulse_tick(self) -> bool:
        """Advance pulse phase and repaint only the icon."""
        self._pulse_phase = (
            self._pulse_phase + _PULSE_INTERVAL_MS / _PULSE_PERIOD_MS
        ) % 1.0
        self.item.icon = self.create_icon(size=self._icon_size)
        if self._notify:
            self._notify()
        return True

    def _snapshot_from_local_samples(self) -> FxSnapshot | None:
        """Build a day-chart snapshot from cached samples for the active pair."""
        points = local_sample_points(
            samples=self._samples,
            base=self._base,
            quote=self._quote,
        )
        if not points:
            return None
        return FxSnapshot(
            base=self._base,
            quote=self._quote,
            rate=points[-1].rate,
            points=points,
            fetched_at=dt.datetime.now(dt.timezone.utc),
        )

    def _snapshot_with_local_samples(self, *, snapshot: FxSnapshot) -> FxSnapshot:
        """Replace a fetched day snapshot's points with local cache points."""
        points = local_sample_points(
            samples=self._samples,
            base=snapshot.base,
            quote=snapshot.quote,
            now=snapshot.fetched_at,
        )
        return FxSnapshot(
            base=snapshot.base,
            quote=snapshot.quote,
            rate=snapshot.rate,
            points=points,
            fetched_at=snapshot.fetched_at,
        )
