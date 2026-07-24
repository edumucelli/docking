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

"""GTK lifecycle glue for the Alarm applet."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.alarm import meta
from docking.applets.alarm.render import render_icon
from docking.applets.alarm.state import (
    RINGING_TICK_SECONDS,
    TICK_SECONDS,
    WEEKDAY_LABELS,
    AlarmPreset,
    AlarmState,
    add_preset,
    dismiss_ringing,
    menu_status_text,
    prefs_from_state,
    preset_summary,
    remove_preset,
    replace_preset,
    set_enabled,
    snooze_ringing,
    state_from_prefs,
    tick,
    tooltip_text,
)
from docking.applets.base import Applet
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.popup import add_cancel_ok_buttons, prepare_dialog_content
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config

ALARM_DIALOG_WIDTH_PX = 360
ALARM_DIALOG_SPACING_PX = 8
ALARM_DIALOG_MARGIN_PX = 12


class AlarmApplet(Applet):
    """Multiple alarm presets with snooze and dismiss actions."""

    id = meta.id
    name = _("Alarm")
    icon_name = "alarm"

    def __init__(self, icon_size: int, config: Config) -> None:
        prefs = config.applet_prefs.get("alarm", {})
        self._state: AlarmState = state_from_prefs(prefs=prefs)
        self._timer_id: int = 0
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, state=self._state, now=self._now())

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(state=self._state, now=self._now())

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._restart_timer()

    def stop(self) -> None:
        self._stop_timer()
        super().stop()

    def on_clicked(self) -> None:
        if self._state.ringing_index is not None:
            self._dismiss()
            return
        self._show_alarm_dialog()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status = [
            disabled_menu_item(menu_status_text(state=self._state, now=self._now()))
        ]

        primary: list[Gtk.MenuItem] = []
        if self._state.ringing_index is not None:
            snooze = Gtk.MenuItem(label=_("Snooze"))
            snooze.connect("activate", lambda _w: self._snooze())
            dismiss = Gtk.MenuItem(label=_("Dismiss"))
            dismiss.connect("activate", lambda _w: self._dismiss())
            primary.extend([snooze, dismiss])

        add_alarm = Gtk.MenuItem(label=_("Add Alarm..."))
        add_alarm.connect("activate", lambda _w: self._show_alarm_dialog())

        manage: list[Gtk.MenuItem] = [add_alarm]
        manage.extend(self._preset_menu_items())

        return menu_sections(status=status, primary=primary, manage=manage, gtk=Gtk)

    def _preset_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []
        for index, preset in enumerate(self._state.presets):
            toggle = Gtk.CheckMenuItem(label=preset_summary(preset))
            toggle.set_active(preset.enabled)
            toggle.connect(
                "toggled",
                lambda widget, selected=index: self._toggle_alarm(
                    index=selected,
                    enabled=widget.get_active(),
                ),
            )
            items.append(toggle)

            edit = Gtk.MenuItem(label=_("Edit {label}...").format(label=preset.label))
            edit.connect(
                "activate",
                lambda _widget, selected=index: self._show_alarm_dialog(index=selected),
            )
            items.append(edit)
        return items

    def _tick(self) -> bool:
        result = tick(state=self._state, now=self._now())
        self._state = result.state
        if result.started_ringing:
            self.item.is_urgent = True
            self.item.last_urgent = GLib.get_monotonic_time()
            Gdk.beep()
            self._save()
            self._restart_timer()
        if result.should_refresh:
            self.present()
        return True

    def _show_alarm_dialog(self, index: int | None = None) -> None:
        preset = (
            self._state.presets[index]
            if index is not None and 0 <= index < len(self._state.presets)
            else AlarmPreset()
        )
        dialog = Gtk.Dialog(
            title=_("Edit Alarm") if index is not None else _("Add Alarm"),
            modal=True,
            destroy_with_parent=True,
        )
        add_cancel_ok_buttons(dialog=dialog, ok_label=_("OK"), cancel_label=_("Cancel"))
        if index is not None:
            dialog.add_button(_("Remove"), Gtk.ResponseType.REJECT)
        content = prepare_dialog_content(
            dialog=dialog,
            width=ALARM_DIALOG_WIDTH_PX,
            spacing=ALARM_DIALOG_SPACING_PX,
            margin=ALARM_DIALOG_MARGIN_PX,
            default_response=Gtk.ResponseType.OK,
            resizable=False,
        )

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        content.pack_start(grid, True, True, 0)

        label_entry = Gtk.Entry()
        label_entry.set_text(preset.label)

        hour_spin = Gtk.SpinButton()
        hour_spin.set_adjustment(Gtk.Adjustment(preset.hour, 0, 23, 1, 1, 0))
        hour_spin.set_numeric(True)

        minute_spin = Gtk.SpinButton()
        minute_spin.set_adjustment(Gtk.Adjustment(preset.minute, 0, 59, 1, 1, 0))
        minute_spin.set_numeric(True)

        enabled = Gtk.CheckButton(label=_("Enabled"))
        enabled.set_active(preset.enabled)

        snooze_spin = Gtk.SpinButton()
        snooze_spin.set_adjustment(
            Gtk.Adjustment(preset.snooze_minutes, 1, 120, 1, 5, 0)
        )
        snooze_spin.set_numeric(True)

        repeat_checks = [Gtk.CheckButton(label=label) for label in WEEKDAY_LABELS]
        for day, check in enumerate(repeat_checks):
            check.set_active(day in preset.repeat_days)

        grid.attach(Gtk.Label(label=_("Label")), 0, 0, 1, 1)
        grid.attach(label_entry, 1, 0, 3, 1)
        grid.attach(Gtk.Label(label=_("Hour")), 0, 1, 1, 1)
        grid.attach(hour_spin, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label=_("Minute")), 2, 1, 1, 1)
        grid.attach(minute_spin, 3, 1, 1, 1)
        grid.attach(Gtk.Label(label=_("Snooze")), 0, 2, 1, 1)
        grid.attach(snooze_spin, 1, 2, 1, 1)
        grid.attach(enabled, 2, 2, 2, 1)
        grid.attach(Gtk.Label(label=_("Repeat")), 0, 3, 1, 1)

        repeat_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for check in repeat_checks:
            repeat_box.pack_start(check, False, False, 0)
        grid.attach(repeat_box, 1, 3, 3, 1)

        def on_response(_dialog: Gtk.Dialog, response_id: int) -> None:
            if response_id == Gtk.ResponseType.OK:
                repeat_days = tuple(
                    day for day, check in enumerate(repeat_checks) if check.get_active()
                )
                updated = AlarmPreset(
                    label=label_entry.get_text(),
                    hour=hour_spin.get_value_as_int(),
                    minute=minute_spin.get_value_as_int(),
                    enabled=enabled.get_active(),
                    repeat_days=repeat_days,
                    snooze_minutes=snooze_spin.get_value_as_int(),
                )
                self._upsert_preset(index=index, preset=updated)
            elif response_id == Gtk.ResponseType.REJECT and index is not None:
                self._state = remove_preset(state=self._state, index=index)
                self._save()
                self.present()
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.show_all()
        label_entry.grab_focus()

    def _upsert_preset(self, *, index: int | None, preset: AlarmPreset) -> None:
        if index is None:
            self._state = add_preset(state=self._state, preset=preset)
        else:
            self._state = replace_preset(state=self._state, index=index, preset=preset)
        self.item.is_urgent = self._state.ringing_index is not None
        self._save()
        self.present()

    def _toggle_alarm(self, *, index: int, enabled: bool) -> None:
        self._state = set_enabled(state=self._state, index=index, enabled=enabled)
        self._save()
        self.present()

    def _snooze(self) -> None:
        self._state = snooze_ringing(state=self._state, now=self._now())
        self.item.is_urgent = False
        self._save()
        self._restart_timer()
        self.present()

    def _dismiss(self) -> None:
        self._state = dismiss_ringing(state=self._state)
        self.item.is_urgent = False
        self._save()
        self._restart_timer()
        self.present()

    def _restart_timer(self) -> None:
        if self._notify is None:
            return
        self._stop_timer()
        interval = (
            RINGING_TICK_SECONDS
            if self._state.ringing_index is not None
            else TICK_SECONDS
        )
        self._timer_id = GLib.timeout_add_seconds(interval, self._tick)

    def _stop_timer(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0

    def _save(self) -> None:
        self.save_prefs(prefs=prefs_from_state(self._state))

    @staticmethod
    def _now() -> dt.datetime:
        return dt.datetime.now().astimezone()
