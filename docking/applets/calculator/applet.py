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

"""GTK lifecycle and popup orchestration for the Calculator applet.

How this applet works

The calculator is intentionally implemented as a transient popup rather than a
persistent dock widget. The icon stays compact in the dock, and the actual
calculator UI is created only when the user asks for it.

That design gives the applet three responsibilities:

1. toggle and position a popup window near the pointer,
2. translate button presses and entry activation into calculator actions,
3. persist the last displayed expression/result so the tool feels stateful
   across dock restarts.

What this module does not do

It does not evaluate expressions itself and it does not draw the dock icon.
Those concerns live in ``state.py`` and ``render.py`` respectively. That split
is important because popup lifecycle code is inherently GTK-heavy, while the
math evaluator should remain testable without any UI dependency.

The result is a small but representative applet module: GTK wiring here, pure
logic elsewhere, and only the minimal persistence needed for a polished user
experience.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gtk

from docking.applets.base import Applet
from docking.applets.calculator import meta
from docking.applets.calculator.render import create_icon
from docking.applets.calculator.state import evaluate, prefs_payload
from docking.applets.popup import create_popup_window, show_wrapped_popup
from docking.core.icons import IconSource
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config

POPUP_PADDING_PX = 10
POPUP_CURSOR_GAP_PX = 20
BUTTON_SPACING_PX = 4
BUTTON_ROWS = (
    ("7", "8", "9", "/"),
    ("4", "5", "6", "*"),
    ("1", "2", "3", "-"),
    ("C", "0", ".", "+"),
    ("(", ")", "\u2190", "="),
)


class CalculatorApplet(Applet):
    """Basic four-function calculator with a popup interface."""

    id = meta.id
    name = _("Calculator")
    icon_name = "accessories-calculator"
    icon_source_options = (IconSource.DOCKING, IconSource.SYSTEM)

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._popup: Gtk.Window | None = None
        self._entry: Gtk.Entry | None = None

        prefs = config.applet_prefs.get("calculator", {}) if config else {}
        self._last_expr = str(prefs.get("last_expression", ""))

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(size=size)

    def refresh_tooltip(self) -> None:
        self.item.name = _("Calculator")

    def on_clicked(self) -> None:
        if self._popup and self._popup.get_visible():
            self._popup.hide()
            return
        self._show_popup()

    def stop(self) -> None:
        if self._popup:
            self._popup.destroy()
            self._popup = None
        super().stop()

    # -- Popup ----------------------------------------------------------------

    def _show_popup(self) -> None:
        if self._popup is None:
            self._popup = create_popup_window()

        show_wrapped_popup(
            window=self._popup,
            content=self._build_popup_content(),
            gap_px=POPUP_CURSOR_GAP_PX,
            anchor=self.popup_anchor,
        )

    def _build_popup_content(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=BUTTON_SPACING_PX)
        box.set_margin_start(POPUP_PADDING_PX)
        box.set_margin_end(POPUP_PADDING_PX)
        box.set_margin_top(POPUP_PADDING_PX)
        box.set_margin_bottom(POPUP_PADDING_PX)

        # Display entry
        self._entry = Gtk.Entry()
        self._entry.set_text(self._last_expr)
        self._entry.set_alignment(1.0)
        self._entry.connect("activate", lambda _: self._do_evaluate())
        font_desc = self._entry.get_pango_context().get_font_description()
        font_desc.set_family("monospace")
        self._entry.override_font(font_desc)
        box.pack_start(self._entry, False, False, 0)

        # Button grid
        for row_labels in BUTTON_ROWS:
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=BUTTON_SPACING_PX,
                homogeneous=True,
            )
            for label in row_labels:
                btn = Gtk.Button(label=label)
                btn.connect("clicked", self._on_button, label)
                row.pack_start(btn, True, True, 0)
            box.pack_start(row, False, False, 0)

        return box

    def _on_button(self, _btn: Gtk.Button, label: str) -> None:
        if not self._entry:
            return
        if label == "C":
            self._entry.set_text("")
        elif label == "\u2190":
            text = self._entry.get_text()
            self._entry.set_text(text[:-1])
        elif label == "=":
            self._do_evaluate()
        else:
            pos = self._entry.get_position()
            self._entry.insert_text(label, pos)
            self._entry.set_position(pos + len(label))

    def _do_evaluate(self) -> None:
        if not self._entry:
            return
        expr = self._entry.get_text()
        result = evaluate(expression=expr)
        self._entry.set_text(result)
        self._entry.set_position(-1)
        self._last_expr = result
        self._save_prefs()

    def _save_prefs(self) -> None:
        self.save_prefs(prefs=prefs_payload(last_expression=self._last_expr))
