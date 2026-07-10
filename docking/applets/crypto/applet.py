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

"""GTK lifecycle glue for the Crypto applet."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.crypto import meta
from docking.applets.crypto.render import render_icon
from docking.applets.crypto.state import (
    REFRESH_INTERVAL_S,
    STARTUP_FETCH_DELAY_S,
    AssetType,
    ChartInterval,
    CryptoAsset,
    CryptoSnapshot,
    append_local_sample,
    asset_key,
    build_tooltip,
    fetch_crypto_snapshot,
    format_change,
    format_price,
    local_sample_points,
    normalize_asset,
    normalize_chart_interval,
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
from docking.applets.popup import add_cancel_ok_buttons, prepare_dialog_content
from docking.applets.worker import BackgroundWorker
from docking.core.math import clamp_index
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="crypto"), applet_id=meta.id)

DIALOG_WIDTH_PX = 280
DIALOG_SPACING_PX = 8
DIALOG_MARGIN_PX = 12
_PULSE_INTERVAL_MS = 60
_PULSE_PERIOD_MS = 1800


class CryptoApplet(Applet):
    """Track selected crypto coins and NFT floor prices."""

    id = meta.id
    name = _("Crypto")
    icon_name = "emblem-money-symbolic"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._startup_fetch_timer_id: int = 0
        self._pulse_timer_id: int = 0
        self._pulse_phase: float = 0.0
        self._fetch_request_id: int = 0
        self._snapshot: CryptoSnapshot | None = None
        self._loading = False
        self._fetch_failed = False
        self._fetch_error = ""
        self._worker = BackgroundWorker(logger=log)

        raw_prefs = config.applet_prefs.get(meta.id, {}) if config else None
        prefs = prefs_from_mapping(raw_prefs)
        self._assets = list(prefs.assets)
        self._active_index = prefs.active_index
        self._chart_interval = prefs.chart_interval
        self._vs_currency = prefs.vs_currency
        self._samples = dict(prefs.samples)
        self._snapshot = self._snapshot_from_local_samples()

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    @property
    def _active_asset(self) -> CryptoAsset:
        return self._assets[self._active_index]

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        phase = self._pulse_phase if self._has_chart_dot() else None
        return render_icon(
            size=size,
            snapshot=self._snapshot,
            asset_symbol=self._active_asset.symbol,
            asset_type=self._active_asset.asset_type,
            fetch_failed=self._fetch_failed,
            pulse_phase=phase,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            asset=self._active_asset,
            snapshot=self._snapshot,
            loading=self._loading,
            fetch_failed=self._fetch_failed,
            error=self._fetch_error,
            chart_interval=self._chart_interval,
            cadence_seconds=REFRESH_INTERVAL_S,
        )

    def on_clicked(self) -> None:
        self._show_asset_dialog()

    def on_scroll(self, direction_up: bool) -> None:
        if len(self._assets) <= 1:
            return
        step = -1 if direction_up else 1
        self._activate_asset_index((self._active_index + step) % len(self._assets))

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status = [disabled_menu_item(self._menu_header(), gtk=Gtk)]
        status.append(
            disabled_menu_item(cadence_label(seconds=REFRESH_INTERVAL_S), gtk=Gtk)
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

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _w: self._fetch_async())

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
        if len(self._assets) > 1:
            display.append(
                radio_submenu(
                    label=_("Selected Assets"),
                    choices=tuple(
                        (_asset_menu_label(asset), index)
                        for index, asset in enumerate(self._assets)
                    ),
                    active_value=self._active_index,
                    on_selected=lambda _widget, value: self._activate_asset_index(
                        value
                    ),
                    gtk=Gtk,
                )
            )

        add = Gtk.MenuItem(label=_("Add Asset..."))
        add.connect("activate", lambda _w: self._show_asset_dialog())

        destructive: list[Gtk.MenuItem] = []
        if len(self._assets) > 1:
            remove = Gtk.MenuItem(label=_("Remove Current Asset"))
            remove.connect("activate", lambda _w: self._remove_active_asset())
            destructive.append(remove)

        return menu_sections(
            status=status,
            refresh=[refresh],
            display=display,
            manage=[add],
            destructive=destructive,
            gtk=Gtk,
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(REFRESH_INTERVAL_S, self._tick)
        self._startup_fetch_timer_id = GLib.timeout_add_seconds(
            STARTUP_FETCH_DELAY_S,
            self._run_startup_fetch,
        )
        self._ensure_pulse_timer()

    def stop(self) -> None:
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
        asset = self._active_asset
        chart_interval = self._chart_interval
        vs_currency = self._vs_currency
        self._loading = True
        self._fetch_failed = False
        self._fetch_error = ""
        self.present()

        self._worker.run(
            name="crypto-fetch",
            fn=lambda: fetch_crypto_snapshot(
                asset=asset,
                chart_interval=chart_interval,
                vs_currency=vs_currency,
            ),
            on_result=lambda snapshot: self._on_fetch_result(
                request_id=request_id,
                snapshot=snapshot,
            ),
            on_error=lambda exc: self._on_fetch_error(request_id=request_id, exc=exc),
        )

    def _on_fetch_result(
        self,
        *,
        request_id: int,
        snapshot: CryptoSnapshot | None,
    ) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        if snapshot is not None:
            self._samples = append_local_sample(
                samples=self._samples,
                asset=snapshot.asset,
                price=snapshot.price,
                now=snapshot.fetched_at,
            )
            if snapshot.asset.asset_type == AssetType.NFT:
                snapshot = self._snapshot_with_local_samples(snapshot=snapshot)
            self._snapshot = snapshot
            self._fetch_error = ""
            self._save_prefs()
        else:
            self._fetch_error = _("No price data")
        self._fetch_failed = snapshot is None
        self._ensure_pulse_timer()
        self.present()
        return False

    def _on_fetch_error(self, *, request_id: int, exc: Exception) -> bool:
        if request_id != self._fetch_request_id:
            return False
        log.bind(action="fetch_error").debug("Crypto fetch failed: %s", exc)
        self._loading = False
        self._fetch_failed = True
        self._fetch_error = str(exc) or exc.__class__.__name__
        self._ensure_pulse_timer()
        self.present()
        return False

    def _on_interval_selected(
        self,
        *,
        widget: Gtk.RadioMenuItem,
        interval: ChartInterval,
    ) -> None:
        if not widget.get_active():
            return
        interval = normalize_chart_interval(interval)
        if interval == self._chart_interval:
            return
        self._chart_interval = interval
        self._snapshot = self._snapshot_from_local_samples()
        self._fetch_failed = False
        self._fetch_error = ""
        self._loading = False
        self._save_prefs()
        self._fetch_async()
        self.present()

    def _add_asset(self, asset: CryptoAsset) -> None:
        if asset in self._assets:
            self._activate_asset_index(self._assets.index(asset))
            return
        key = asset_key(asset)
        for index, current in enumerate(self._assets):
            if asset_key(current) == key:
                self._assets[index] = asset
                self._activate_asset_index(index)
                return
        self._assets.append(asset)
        self._active_index = len(self._assets) - 1
        self._asset_changed()

    def _activate_asset_index(self, index: int) -> None:
        if not self._assets:
            return
        index = clamp_index(index, len(self._assets))
        if index == self._active_index:
            return
        self._active_index = index
        self._asset_changed()

    def _remove_active_asset(self) -> None:
        if len(self._assets) <= 1:
            return
        removed = self._assets[self._active_index]
        del self._assets[self._active_index]
        self._samples.pop(asset_key(removed), None)
        self._active_index = min(self._active_index, len(self._assets) - 1)
        self._asset_changed()

    def _asset_changed(self) -> None:
        self._snapshot = self._snapshot_from_local_samples()
        self._fetch_failed = False
        self._fetch_error = ""
        self._loading = False
        self._save_prefs()
        self._fetch_async()
        self.present()

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=prefs_payload(
                assets=tuple(self._assets),
                active_index=self._active_index,
                chart_interval=self._chart_interval,
                vs_currency=self._vs_currency,
                samples=self._samples,
            )
        )

    def _menu_header(self) -> str:
        if not self._snapshot:
            return _asset_menu_label(self._active_asset)
        return _("{asset}: {price} ({change})").format(
            asset=_asset_menu_label(self._snapshot.asset),
            price=format_price(
                self._snapshot.price,
                vs_currency=self._snapshot.vs_currency,
            ),
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

    def _show_asset_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Add Crypto Asset"),
            modal=True,
            destroy_with_parent=True,
        )
        add_cancel_ok_buttons(dialog=dialog)
        box = prepare_dialog_content(
            dialog=dialog,
            width=DIALOG_WIDTH_PX,
            spacing=DIALOG_SPACING_PX,
            margin=DIALOG_MARGIN_PX,
            default_response=Gtk.ResponseType.OK,
        )

        type_combo = Gtk.ComboBoxText()
        type_combo.append_text(AssetType.COIN.value)
        type_combo.append_text(AssetType.NFT.value)
        type_combo.set_active(0)
        id_entry = Gtk.Entry()
        id_entry.set_placeholder_text(_("bitcoin or cryptopunks"))
        symbol_entry = Gtk.Entry()
        symbol_entry.set_placeholder_text(_("BTC"))
        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text(_("Bitcoin"))

        for label, widget in (
            (_("Type"), type_combo),
            (_("CoinGecko ID"), id_entry),
            (_("Symbol"), symbol_entry),
            (_("Name"), name_entry),
        ):
            box.pack_start(Gtk.Label(label=label), False, False, 0)
            box.pack_start(widget, False, False, 0)

        dialog.show_all()
        id_entry.grab_focus()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            asset = normalize_asset(
                asset_type=type_combo.get_active_text(),
                asset_id=id_entry.get_text(),
                symbol=symbol_entry.get_text(),
                name=name_entry.get_text(),
            )
            self._add_asset(asset)
        dialog.destroy()

    def _has_chart_dot(self) -> bool:
        return bool(self._snapshot and self._snapshot.points)

    def _ensure_pulse_timer(self) -> None:
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
        self._pulse_phase = (
            self._pulse_phase + _PULSE_INTERVAL_MS / _PULSE_PERIOD_MS
        ) % 1.0
        self.item.icon = self.create_icon(size=self._icon_size)
        if self._notify:
            self._notify()
        return True

    def _snapshot_from_local_samples(self) -> CryptoSnapshot | None:
        points = local_sample_points(
            samples=self._samples,
            asset=self._active_asset,
        )
        if not points:
            return None
        return CryptoSnapshot(
            asset=self._active_asset,
            vs_currency=self._vs_currency,
            price=points[-1].price,
            points=points,
            fetched_at=dt.datetime.now(dt.timezone.utc),
        )

    def _snapshot_with_local_samples(
        self,
        *,
        snapshot: CryptoSnapshot,
    ) -> CryptoSnapshot:
        points = local_sample_points(
            samples=self._samples,
            asset=snapshot.asset,
            now=snapshot.fetched_at,
        )
        return CryptoSnapshot(
            asset=snapshot.asset,
            vs_currency=snapshot.vs_currency,
            price=snapshot.price,
            points=points,
            fetched_at=snapshot.fetched_at,
            change_pct_24h=snapshot.change_pct_24h,
        )


def _asset_menu_label(asset: CryptoAsset) -> str:
    prefix = "NFT" if asset.asset_type == AssetType.NFT else "Coin"
    return f"{prefix}: {asset.symbol}"
