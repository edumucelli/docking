"""GTK lifecycle and popup UI for the Unit Converter applet.

What makes this applet different

Most converter logic is pure math, but the user experience depends on a fair
amount of UI coordination:

- a transient popup positioned near the pointer,
- category and unit selectors that repopulate each other,
- a live result label that updates on every input change,
- background currency-rate loading that must not block GTK.

This module owns those orchestration details. It is the adapter between the
state layer and the popup controls.

Why background work appears here

Currency conversion is the one category that needs remote data. The applet uses
``BackgroundWorker`` during startup so the dock can appear immediately while
rates load off the main thread. Once those rates arrive, the popup can expose
currency as just another category without changing the rest of the UI model.

What stays out of this module

Actual conversion rules, temperature special cases, result formatting, and rate
fetching live in ``state.py``. The icon remains procedural in ``render.py``.
That split keeps GTK code focused on user interaction rather than business
logic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gtk

from docking.applets.base import Applet
from docking.applets.popup import create_popup_window, show_wrapped_popup
from docking.applets.unitconverter import meta
from docking.applets.unitconverter.render import create_icon
from docking.applets.unitconverter.state import (
    Unit,
    convert,
    fetch_currency_rates,
    format_result,
    get_categories,
    get_units,
    prefs_payload,
    set_currency_units,
)
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="unitconverter"), applet_id=meta.id)

POPUP_PADDING_PX = 12
POPUP_CURSOR_GAP_PX = 20
POPUP_WIDTH_PX = 280


class UnitConverterApplet(Applet):
    """Instant unit conversion via a click-to-open popup."""

    id = meta.id
    name = _("Unit Converter")
    icon_name = "emblem-synchronizing-symbolic"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._popup: Gtk.Window | None = None
        self._result_label: Gtk.Label | None = None
        self._from_combo: Gtk.ComboBoxText | None = None
        self._to_combo: Gtk.ComboBoxText | None = None
        self._entry: Gtk.Entry | None = None
        self._cat_combo: Gtk.ComboBoxText | None = None
        self._worker = BackgroundWorker(logger=log)

        prefs = config.applet_prefs.get("unitconverter", {}) if config else {}
        self._cat_idx = int(prefs.get("category_index", 0))
        self._from_idx = int(prefs.get("from_index", 0))
        self._to_idx = int(prefs.get("to_index", 1))

        cats = get_categories()
        self._cat_idx = max(0, min(self._cat_idx, len(cats) - 1))

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(size=size)

    def refresh_tooltip(self) -> None:
        self.item.name = _("Unit Converter")

    def on_clicked(self) -> None:
        if self._popup and self._popup.get_visible():
            self._popup.hide()
            return
        self._show_popup()

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._worker.run(
            name="currency-fetch",
            fn=fetch_currency_rates,
            on_result=self._on_currency_result,
        )

    def stop(self) -> None:
        if self._popup:
            self._popup.destroy()
            self._popup = None
        super().stop()

    def _on_currency_result(self, units: tuple[Unit, ...] | None) -> bool:
        if units:
            set_currency_units(units)
        return False

    # -- Popup ----------------------------------------------------------------

    def _show_popup(self) -> None:
        if self._popup is None:
            self._popup = create_popup_window()

        show_wrapped_popup(
            window=self._popup,
            content=self._build_popup_content(),
            gap_px=POPUP_CURSOR_GAP_PX,
        )

    def _build_popup_content(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(POPUP_PADDING_PX)
        box.set_margin_end(POPUP_PADDING_PX)
        box.set_margin_top(POPUP_PADDING_PX)
        box.set_margin_bottom(POPUP_PADDING_PX)
        box.set_size_request(POPUP_WIDTH_PX, -1)

        # Category selector
        cats = get_categories()
        self._cat_combo = Gtk.ComboBoxText()
        for cat in cats:
            self._cat_combo.append_text(cat.value)
        self._cat_idx = max(0, min(self._cat_idx, len(cats) - 1))
        self._cat_combo.set_active(self._cat_idx)
        self._cat_combo.connect("changed", self._on_category_changed)
        box.pack_start(self._cat_combo, False, False, 0)

        # From / Swap / To row
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self._from_combo = Gtk.ComboBoxText()
        self._from_combo.set_hexpand(True)
        row.pack_start(self._from_combo, True, True, 0)

        swap_btn = Gtk.Button(label="\u21c4")
        swap_btn.set_tooltip_text(_("Swap units"))
        swap_btn.connect("clicked", self._on_swap)
        row.pack_start(swap_btn, False, False, 0)

        self._to_combo = Gtk.ComboBoxText()
        self._to_combo.set_hexpand(True)
        row.pack_start(self._to_combo, True, True, 0)

        box.pack_start(row, False, False, 0)

        self._populate_unit_combos()
        self._from_combo.connect("changed", self._on_unit_changed)
        self._to_combo.connect("changed", self._on_unit_changed)

        # Input
        self._entry = Gtk.Entry()
        self._entry.set_text("1")
        self._entry.set_placeholder_text(_("Value"))
        self._entry.connect("changed", self._on_input_changed)
        box.pack_start(self._entry, False, False, 0)

        # Result
        self._result_label = Gtk.Label()
        self._result_label.set_selectable(True)
        self._result_label.set_xalign(0.5)
        box.pack_start(self._result_label, False, False, 0)

        self._update_result()
        return box

    def _populate_unit_combos(self) -> None:
        cats = get_categories()
        cat = cats[self._cat_idx]
        units = get_units(cat)

        if self._from_combo:
            self._from_combo.remove_all()
            for u in units:
                self._from_combo.append_text(f"{u.name} ({u.symbol})")
            idx = max(0, min(self._from_idx, len(units) - 1))
            self._from_combo.set_active(idx)

        if self._to_combo:
            self._to_combo.remove_all()
            for u in units:
                self._to_combo.append_text(f"{u.name} ({u.symbol})")
            idx = max(0, min(self._to_idx, len(units) - 1))
            self._to_combo.set_active(idx)

    def _on_category_changed(self, combo: Gtk.ComboBoxText) -> None:
        idx = combo.get_active()
        if idx < 0:
            return
        cats = get_categories()
        self._cat_idx = idx
        self._from_idx = 0
        self._to_idx = min(1, len(get_units(cats[idx])) - 1)
        self._populate_unit_combos()
        self._update_result()
        self._save_prefs()

    def _on_swap(self, _btn: Gtk.Button) -> None:
        if not self._from_combo or not self._to_combo:
            return
        f = self._from_combo.get_active()
        t = self._to_combo.get_active()
        self._from_combo.set_active(t)
        self._to_combo.set_active(f)

    def _on_unit_changed(self, _combo: Gtk.ComboBoxText) -> None:
        if self._from_combo:
            self._from_idx = max(0, self._from_combo.get_active())
        if self._to_combo:
            self._to_idx = max(0, self._to_combo.get_active())
        self._update_result()
        self._save_prefs()

    def _on_input_changed(self, _entry: Gtk.Entry) -> None:
        self._update_result()

    def _update_result(self) -> None:
        if not self._result_label or not self._entry:
            return

        text = self._entry.get_text().strip().replace(",", "")
        cats = get_categories()
        cat = cats[self._cat_idx]
        units = get_units(cat)
        if not units:
            self._result_label.set_markup(
                '<span color="#ff6b6b">No units available</span>'
            )
            return
        fi = max(0, min(self._from_idx, len(units) - 1))
        ti = max(0, min(self._to_idx, len(units) - 1))

        try:
            value = float(text)
        except ValueError as exc:
            log.debug("Invalid unit-converter input %r: %s", text, exc)
            self._result_label.set_markup('<span color="#ff6b6b">Enter a number</span>')
            return

        result = convert(
            value=value,
            from_unit=units[fi],
            to_unit=units[ti],
            category=cat,
        )
        formatted = format_result(result)
        symbol = units[ti].symbol
        self._result_label.set_markup(
            f'<span size="large" weight="bold">{formatted} {symbol}</span>'
        )

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=prefs_payload(
                category_index=self._cat_idx,
                from_index=self._from_idx,
                to_index=self._to_idx,
            )
        )
