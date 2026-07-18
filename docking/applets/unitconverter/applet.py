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
from docking.applets.popup import (
    entry_completion_combo,
    prepare_dialog_content,
)
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
from docking.core.math import clamp_index
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="unitconverter"), applet_id=meta.id)

POPUP_PADDING_PX = 12
POPUP_CURSOR_GAP_PX = 20
POPUP_WIDTH_PX = 280


def _unit_label(unit: Unit) -> str:
    return f"{unit.name} ({unit.symbol})"


def _unit_label_matches(text: str, label: str) -> bool:
    """Match "Unit Name (sym)" by visible label, unit name, or symbol prefix."""
    needle = text.strip().casefold()
    if not needle:
        return True
    normalized_label = label.strip().casefold()
    if normalized_label.startswith(needle):
        return True
    name, symbol = _split_unit_label(label=label)
    return name.casefold().startswith(needle) or symbol.casefold().startswith(needle)


def _split_unit_label(*, label: str) -> tuple[str, str]:
    """Split "Unit Name (sym)" into name and symbol."""
    text = label.strip()
    if text.endswith(")") and "(" in text:
        name, symbol = text.rsplit("(", 1)
        return name.strip(), symbol[:-1].strip()
    return text, ""


class UnitConverterApplet(Applet):
    """Instant unit conversion via a click-to-open popup."""

    id = meta.id
    name = _("Unit Converter")
    icon_name = "emblem-synchronizing-symbolic"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._popup: Gtk.Dialog | None = None
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
        self._cat_idx = clamp_index(self._cat_idx, len(cats))

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

    # -- Dialog ---------------------------------------------------------------

    def _show_popup(self) -> None:
        if self._popup is None:
            self._popup = Gtk.Dialog(
                title=_("Unit Converter"),
                destroy_with_parent=True,
            )
            self.register_popup_surface(self._popup)
            self._popup.connect("delete-event", self._on_popup_delete)

        content = prepare_dialog_content(
            dialog=self._popup,
            width=POPUP_WIDTH_PX,
            spacing=0,
            margin=0,
            resizable=False,
        )
        for child in content.get_children():
            content.remove(child)
        content.add(self._build_popup_content())
        self._popup.show_all()
        self._popup.present()

    def _on_popup_delete(self, _dialog: Gtk.Dialog, _event) -> bool:
        if self._popup:
            self._popup.hide()
        return True

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
        self._cat_idx = clamp_index(self._cat_idx, len(cats))
        self._cat_combo.set_active(self._cat_idx)
        self._cat_combo.connect("changed", self._on_category_changed)
        box.pack_start(self._cat_combo, False, False, 0)

        # From / Swap / To row
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self._from_combo = entry_completion_combo(matches=_unit_label_matches)
        self._from_combo.set_hexpand(True)
        row.pack_start(self._from_combo, True, True, 0)

        swap_btn = Gtk.Button(label="\u21c4")
        swap_btn.set_tooltip_text(_("Swap units"))
        swap_btn.connect("clicked", self._on_swap)
        row.pack_start(swap_btn, False, False, 0)

        self._to_combo = entry_completion_combo(matches=_unit_label_matches)
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
                self._from_combo.append_text(_unit_label(u))
            idx = clamp_index(self._from_idx, len(units))
            self._from_combo.set_active(idx)

        if self._to_combo:
            self._to_combo.remove_all()
            for u in units:
                self._to_combo.append_text(_unit_label(u))
            idx = clamp_index(self._to_idx, len(units))
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
            self._from_idx = self._unit_index_from_combo(
                combo=self._from_combo,
                fallback=self._from_idx,
            )
        if self._to_combo:
            self._to_idx = self._unit_index_from_combo(
                combo=self._to_combo,
                fallback=self._to_idx,
            )
        self._update_result()
        self._save_prefs()

    def _unit_index_from_combo(self, *, combo: Gtk.ComboBoxText, fallback: int) -> int:
        """Resolve either selected or typed unit text into the current unit index."""
        typed = combo.get_child().get_text().strip()
        units = get_units(get_categories()[self._cat_idx])
        for index, unit in enumerate(units):
            if _unit_label_matches(typed, _unit_label(unit)):
                return index
        return max(0, combo.get_active(), fallback)

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
        fi = clamp_index(self._from_idx, len(units))
        ti = clamp_index(self._to_idx, len(units))

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
