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

"""Startup usage tip popup UI.

This controller is only one source in the startup popup system. It owns tip
selection and its GTK surface, while `StartupPopupCoordinator` owns whether the
tip is allowed to appear now or must wait behind higher-priority startup
popups.

Tip state is consumed only when the popup is actually shown. That matters when
updates or seasonal popups win startup arbitration: a skipped or expired tip
should not be marked as seen.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from docking.core.position import is_horizontal
from docking.core.tips import StartupTip, select_startup_tip
from docking.i18n import _
from docking.log import get_logger
from docking.ui.display import clamp_popup, window_screen_position
from docking.ui.popup_surface import (
    configure_transparent_startup_popup_window,
    wrap_startup_popup_content,
)
from docking.ui.tooltip import compute_tooltip_position

if TYPE_CHECKING:
    from docking.core.config import Config

log = get_logger("tips")

STARTUP_TIP_POPUP_ID = "startup-tips"
STARTUP_TIP_POPUP_PRIORITY = 30
STARTUP_TIP_DELAY_S = 12
STARTUP_TIP_MAX_PRIORITY_WAIT_S = 30
STARTUP_TIP_POPUP_GAP_PX = 16
STARTUP_TIP_SPACING_PX = 10
STARTUP_TIP_MARGIN_PX = 14
STARTUP_TIP_WIDTH_CHARS = 48
STARTUP_TIP_ICON_NAME = "help-hint"
STARTUP_TIP_ICON_FALLBACK = "dialog-information"


class StartupTipsController:
    """Schedule and show one startup usage tip when the coordinator allows it."""

    source_id = STARTUP_TIP_POPUP_ID
    priority = STARTUP_TIP_POPUP_PRIORITY
    max_wait_seconds: int | None = STARTUP_TIP_MAX_PRIORITY_WAIT_S

    def __init__(
        self,
        *,
        window: Gtk.Window,
        config: Config,
        state_path: Path | str | None = None,
        chooser: Callable[[Sequence[StartupTip]], StartupTip] | None = None,
    ) -> None:
        self._window = window
        self._config = config
        self._state_path = Path(state_path) if state_path is not None else None
        self._chooser = chooser
        self._start_source_id = 0
        self._popup: Gtk.Window | None = None
        self._current_tip: StartupTip | None = None
        self._show_on_startup_check: Gtk.CheckButton | None = None
        self._request_show: Callable[[str], None] | None = None
        self._visibility_changed: Callable[[str, bool], None] | None = None

    def start(
        self,
        request_show: Callable[[str], None] | None = None,
        visibility_changed: Callable[[str, bool], None] | None = None,
    ) -> None:
        """Schedule the startup tip check once per process."""
        if self._start_source_id or not self._config.startup_tips_enabled:
            return
        self._request_show = request_show
        self._visibility_changed = visibility_changed
        self._start_source_id = GLib.timeout_add_seconds(
            STARTUP_TIP_DELAY_S,
            self._on_startup_delay_elapsed,
        )

    def stop(self) -> None:
        """Cancel pending work and close the popup."""
        if self._start_source_id:
            GLib.source_remove(self._start_source_id)
            self._start_source_id = 0
        self._destroy_popup()

    def _on_startup_delay_elapsed(self) -> bool:
        self._start_source_id = 0
        if not self._config.startup_tips_enabled:
            return False
        if self._request_show is not None:
            self._request_show(self.source_id)
        else:
            self.show_pending()
        return False

    def show_pending(self) -> bool:
        """Select, consume, and show the next startup tip."""
        if not self._window.get_realized():
            log.debug("Skipping startup tip because dock window is not realized")
            return False
        tip = select_startup_tip(
            enabled=self._config.startup_tips_enabled,
            path=self._state_path,
            chooser=self._chooser,
        )
        if tip is None:
            return False
        return self._show_popup(tip=tip)

    def _show_popup(self, *, tip: StartupTip) -> bool:
        if not self._window.get_realized():
            log.debug("Skipping startup tip because dock window is not realized")
            return False
        self._current_tip = tip
        if self._popup is None:
            popup = Gtk.Window(type=Gtk.WindowType.POPUP)
            popup.set_decorated(False)
            popup.set_skip_taskbar_hint(True)
            popup.set_resizable(False)
            popup.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
            configure_transparent_startup_popup_window(popup)
            popup.set_transient_for(self._window)
            popup.connect("destroy", self._on_popup_destroy)
            self._popup = popup
        else:
            child = self._popup.get_child()
            if child is not None:
                self._popup.remove(child)

        self._popup.add(self._build_popup_content(tip=tip))
        self._popup.show_all()
        self._notify_visible(True)
        self._position_popup()
        return True

    def _build_popup_content(self, *, tip: StartupTip) -> Gtk.Widget:
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=STARTUP_TIP_SPACING_PX + 2,
        )
        box.set_border_width(STARTUP_TIP_MARGIN_PX)
        self._add_style_class(box, "startup-tip-content")

        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=STARTUP_TIP_SPACING_PX + 2,
        )
        icon = self._build_tip_icon()
        icon.set_valign(Gtk.Align.START)
        header.pack_start(icon, False, False, 0)

        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=max(4, STARTUP_TIP_SPACING_PX // 2),
        )
        text.set_hexpand(True)

        eyebrow = Gtk.Label(label=_("Tip of the day"))
        eyebrow.set_xalign(0.0)
        self._add_style_class(eyebrow, "dim-label")
        text.pack_start(eyebrow, False, False, 0)

        title = Gtk.Label()
        title.set_xalign(0.0)
        title.set_line_wrap(True)
        title.set_max_width_chars(STARTUP_TIP_WIDTH_CHARS)
        title.set_markup(f"<b>{escape(tip.title)}</b>")
        text.pack_start(title, False, False, 0)

        body = Gtk.Label(label=tip.body)
        body.set_xalign(0.0)
        body.set_line_wrap(True)
        body.set_max_width_chars(STARTUP_TIP_WIDTH_CHARS)
        text.pack_start(body, False, False, 0)

        header.pack_start(text, True, True, 0)
        box.pack_start(header, False, False, 0)

        buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=STARTUP_TIP_SPACING_PX,
        )
        show_on_startup = Gtk.CheckButton(label=_("Show tips on startup"))
        show_on_startup.set_active(True)
        self._show_on_startup_check = show_on_startup
        next_tip = Gtk.Button(label=_("Next Tip"))
        close = Gtk.Button(label=_("Close"))
        next_tip.connect("clicked", self._on_next_tip)
        close.connect("clicked", self._on_close)
        buttons.pack_start(show_on_startup, True, True, 0)
        buttons.pack_start(next_tip, False, False, 0)
        buttons.pack_start(close, False, False, 0)
        box.pack_start(buttons, False, False, 0)

        return wrap_startup_popup_content(box)

    def _build_tip_icon(self) -> Gtk.Image:
        icon_theme = Gtk.IconTheme.get_default()
        icon_name = STARTUP_TIP_ICON_NAME
        if icon_theme is not None and not icon_theme.has_icon(icon_name):
            icon_name = STARTUP_TIP_ICON_FALLBACK
        return Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)

    @staticmethod
    def _add_style_class(widget: Gtk.Widget, class_name: str) -> None:
        widget.get_style_context().add_class(class_name)

    def _position_popup(self) -> None:
        if self._popup is None:
            return
        window_pos = window_screen_position(self._window)
        win_x, win_y = window_pos.x, window_pos.y
        win_w, win_h = self._window.get_size()
        pref = self._popup.get_preferred_size()[1]
        popup_w = max(pref.width, 1)
        popup_h = max(pref.height, 1)
        pos = self._window.config.pos
        if is_horizontal(pos):
            anchor_x = win_x + win_w / 2
            anchor_y = win_y if pos.value == "bottom" else win_y + win_h
        else:
            anchor_x = win_x + win_w if pos.value == "left" else win_x
            anchor_y = win_y + win_h / 2
        popup_x, popup_y = compute_tooltip_position(
            pos=pos,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            tooltip_w=popup_w,
            tooltip_h=popup_h,
            gap=STARTUP_TIP_POPUP_GAP_PX,
        )
        clamped = clamp_popup(self._popup, popup_x, popup_y, popup_w, popup_h)
        self._popup.move(clamped.x, clamped.y)

    def _on_never_show(self, _button: Gtk.Button) -> None:
        self._config.startup_tips_enabled = False
        self._config.save()
        self._destroy_popup()

    def _on_next_tip(self, _button: Gtk.Button) -> None:
        self.show_pending()

    def _on_close(self, _button: Gtk.Button) -> None:
        self._save_startup_tip_visibility_choice()
        if self._popup is not None:
            self._popup.hide()
        self._notify_visible(False)

    def _on_popup_destroy(self, _popup: Gtk.Window) -> None:
        self._popup = None
        self._notify_visible(False)

    def _destroy_popup(self) -> None:
        if self._popup is not None:
            popup = self._popup
            self._popup = None
            popup.destroy()
        self._notify_visible(False)

    def _save_startup_tip_visibility_choice(self) -> None:
        if self._show_on_startup_check is None:
            return
        if self._show_on_startup_check.get_active():
            return
        self._config.startup_tips_enabled = False
        self._config.save()

    def _notify_visible(self, visible: bool) -> None:
        if self._visibility_changed is not None:
            self._visibility_changed(self.source_id, visible)
