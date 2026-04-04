"""GTK lifecycle glue for Clock applet."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib, Gtk

from docking.applets.base import Applet
from docking.applets.clock import meta
from docking.applets.clock.render import render_icon
from docking.applets.clock.state import (
    build_tooltip,
    check_alarm,
    compute_alarm_target,
    load_prefs,
    save_payload,
)
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config

ALARM_DIALOG_CONTENT_SPACING_PX = 8
ALARM_DIALOG_MARGIN_PX = 12


class ClockApplet(Applet):
    """Displays current time as an analog clock face or digital readout."""

    id = meta.id
    name = _("Clock")
    icon_name = "clock"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer = _ClockTimer()

        prefs = config.applet_prefs.get("clock", {}) if config else None
        self._raw_alarm_target = self._prefs_alarm_target(prefs)
        (
            self._show_digital,
            self._show_military,
            self._show_date,
            self._show_seconds,
            self._alarm_target,
        ) = load_prefs(prefs=prefs, now_ts=time.time())
        self._last_minute = time.localtime().tm_min

        super().__init__(icon_size=icon_size, config=config)
        self.present()
        if self._raw_alarm_target != self._alarm_target:
            self._save_prefs()

    def create_icon(self, size: int):
        """Render clock icon in current mode."""
        now = time.localtime()
        return render_icon(
            size=size,
            now=now,
            show_digital=self._show_digital,
            show_military=self._show_military,
            show_date=self._show_date,
            show_seconds=self._show_seconds,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            now=time.localtime(),
            is_24h=self._show_military,
            alarm_target=self._alarm_target,
        )

    def on_clicked(self) -> None:
        if self.item.is_urgent:
            self._acknowledge_alarm()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Clock display, seconds, and one-shot alarm controls."""
        items: list[Gtk.MenuItem] = []

        digital = Gtk.CheckMenuItem(label=_("Digital Clock"))
        digital.set_active(self._show_digital)
        digital.connect("toggled", self._on_toggle_digital)
        items.append(digital)

        military = Gtk.CheckMenuItem(label=_("24-Hour Clock"))
        military.set_active(self._show_military)
        military.connect("toggled", self._on_toggle_military)
        items.append(military)

        date = Gtk.CheckMenuItem(label=_("Show Date"))
        date.set_active(self._show_date)
        date.set_sensitive(self._show_digital)
        date.connect("toggled", self._on_toggle_date)
        items.append(date)

        seconds = Gtk.CheckMenuItem(label=_("Show Seconds"))
        seconds.set_active(self._show_seconds)
        seconds.connect("toggled", self._on_toggle_seconds)
        items.append(seconds)

        items.append(Gtk.SeparatorMenuItem())

        set_alarm = Gtk.MenuItem(label=_("Set Alarm..."))
        set_alarm.connect("activate", lambda _w: self._show_alarm_dialog())
        items.append(set_alarm)

        if self._alarm_target is not None:
            clear_alarm = Gtk.MenuItem(label=_("Clear Alarm"))
            clear_alarm.connect("activate", lambda _w: self._clear_alarm())
            items.append(clear_alarm)

        if self.item.is_urgent:
            acknowledge = Gtk.MenuItem(label=_("Acknowledge Alarm"))
            acknowledge.connect("activate", lambda _w: self._acknowledge_alarm())
            items.append(acknowledge)

        return items

    def _on_toggle_digital(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_digital = widget.get_active()
        self._save_prefs()
        self.present()

    def _on_toggle_military(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_military = widget.get_active()
        self._save_prefs()
        self.present()

    def _on_toggle_date(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_date = widget.get_active()
        self._save_prefs()
        self.present()

    def _on_toggle_seconds(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_seconds = widget.get_active()
        self._last_minute = time.localtime().tm_min
        self._save_prefs()
        self.present()

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=save_payload(
                show_digital=self._show_digital,
                show_military=self._show_military,
                show_date=self._show_date,
                show_seconds=self._show_seconds,
                alarm_target=self._alarm_target,
            )
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer.start(callback=self._on_tick)

    def stop(self) -> None:
        self._timer.stop()
        super().stop()

    def _on_tick(self) -> None:
        now = time.localtime()
        now_ts = time.time()
        should_present = False

        if check_alarm(now_ts=now_ts, alarm_target=self._alarm_target):
            self._trigger_alarm()
            should_present = True

        if self._show_seconds:
            should_present = True
        elif now.tm_min != self._last_minute:
            self._last_minute = now.tm_min
            should_present = True

        if should_present:
            self.present()

    def _trigger_alarm(self) -> None:
        self.item.is_urgent = True
        self.item.last_urgent = GLib.get_monotonic_time()
        self._alarm_target = None
        self._save_prefs()

    def _clear_alarm(self) -> None:
        self._alarm_target = None
        self.item.is_urgent = False
        self._save_prefs()
        self.present()

    def _acknowledge_alarm(self) -> None:
        self.item.is_urgent = False
        self.present()

    def _show_alarm_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Set Alarm"),
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.set_position(Gtk.WindowPosition.MOUSE)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("OK"), Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(ALARM_DIALOG_CONTENT_SPACING_PX)
        content.set_margin_start(ALARM_DIALOG_MARGIN_PX)
        content.set_margin_end(ALARM_DIALOG_MARGIN_PX)
        content.set_margin_top(ALARM_DIALOG_MARGIN_PX)
        content.set_margin_bottom(ALARM_DIALOG_MARGIN_PX)

        current = time.localtime(self._alarm_target or (time.time() + 60))

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        content.pack_start(grid, True, True, 0)

        hour_spin = Gtk.SpinButton()
        hour_spin.set_adjustment(
            Gtk.Adjustment(
                value=current.tm_hour,
                lower=0,
                upper=23,
                step_increment=1,
                page_increment=1,
            )
        )
        hour_spin.set_numeric(True)

        minute_spin = Gtk.SpinButton()
        minute_spin.set_adjustment(
            Gtk.Adjustment(
                value=current.tm_min,
                lower=0,
                upper=59,
                step_increment=1,
                page_increment=1,
            )
        )
        minute_spin.set_numeric(True)

        grid.attach(Gtk.Label(label=_("Hour")), 0, 0, 1, 1)
        grid.attach(hour_spin, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label=_("Minute")), 0, 1, 1, 1)
        grid.attach(minute_spin, 1, 1, 1, 1)

        def on_response(_dlg: Gtk.Dialog, response_id: int) -> None:
            if response_id == Gtk.ResponseType.OK:
                self._set_alarm(
                    hour=hour_spin.get_value_as_int(),
                    minute=minute_spin.get_value_as_int(),
                )
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.show_all()

    def _set_alarm(self, *, hour: int, minute: int) -> None:
        self._alarm_target = compute_alarm_target(
            now_ts=time.time(),
            hour=hour,
            minute=minute,
        )
        self.item.is_urgent = False
        self._save_prefs()
        self.present()

    @staticmethod
    def _prefs_alarm_target(prefs: Mapping[str, Any] | None) -> int | None:
        if not prefs:
            return None
        raw = prefs.get("alarm_target")
        return raw if isinstance(raw, int) else None


class _ClockTimer:
    """1-second GLib timer that delegates tick policy to the applet."""

    def __init__(self) -> None:
        self._timer_id: int = 0
        self._callback: Callable[[], None] | None = None

    def start(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self._callback = None

    def _tick(self) -> bool:
        if self._callback:
            self._callback()
        return True
