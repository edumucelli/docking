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

"""GTK lifecycle glue for Cam Shield applet."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.camshield import meta
from docking.applets.camshield.render import render_icon
from docking.applets.camshield.state import (
    DEFAULT_POLL_INTERVAL_S,
    CamshieldState,
    build_tooltip,
    holder_label,
    probe_camera_state,
)
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="camshield"), applet_id=meta.id)

_PULSE_INTERVAL_MS = 60  # Match Desk Presence: smooth enough without taxing CPU.
_PULSE_PERIOD_MS = 1800
_HELPER_BIN = "docking-camshield-helper"
_PKEXEC_BIN = "pkexec"
_SOURCE_HELPER = Path(__file__).with_name("helper.py")


class CamshieldApplet(Applet):
    """Show when any process is holding a camera device."""

    id = meta.id
    name = _("Cam Shield")
    icon_name = "camera-web"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._state: CamshieldState = probe_camera_state()
        self._timer_id: int = 0
        self._pulse_timer_id: int = 0
        self._pulse_phase: float = 0.0
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        phase = self._pulse_phase if self._state.active else None
        return render_icon(
            size=size,
            available=self._state.available,
            active=self._state.active,
            pulse_phase=phase,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(self._state)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        GLib.idle_add(self._refresh_once)
        self._timer_id = GLib.timeout_add_seconds(
            DEFAULT_POLL_INTERVAL_S,
            self._tick,
        )
        self._ensure_pulse_timer()

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._pulse_timer_id:
            GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = 0
        super().stop()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = []
        if not self._state.available:
            status.append(disabled_menu_item(_("No camera devices found"), gtk=Gtk))
        elif not self._state.active:
            status.append(disabled_menu_item(_("Camera idle"), gtk=Gtk))
        else:
            status.append(disabled_menu_item(_("Camera active"), gtk=Gtk))
            for holder in self._state.holders:
                status.append(disabled_menu_item(holder_label(holder), gtk=Gtk))

        lock = Gtk.MenuItem(label=_("Lock Camera"))
        lock.set_sensitive(self._helper_available())
        lock.connect("activate", lambda _w: self._run_helper_action("lock"))

        unlock = Gtk.MenuItem(label=_("Unlock Camera"))
        unlock.set_sensitive(self._helper_available())
        unlock.connect("activate", lambda _w: self._run_helper_action("unlock"))

        if not self._helper_available():
            status.append(
                disabled_menu_item(_("Camera lock helper unavailable"), gtk=Gtk)
            )

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _w: self._refresh_now())
        return menu_sections(
            status=status,
            primary=[lock, unlock],
            refresh=[refresh],
            gtk=Gtk,
        )

    def _refresh_once(self) -> bool:
        self._refresh_now()
        return False

    def _refresh_now(self) -> None:
        self._state = probe_camera_state()
        self._ensure_pulse_timer()
        self.present()

    def _tick(self) -> bool:
        self._refresh_now()
        return True

    def _ensure_pulse_timer(self) -> None:
        """Run the red-dot pulse only while a camera device is active."""
        if self._state.active and not self._pulse_timer_id:
            self._pulse_timer_id = GLib.timeout_add(
                _PULSE_INTERVAL_MS,
                self._pulse_tick,
            )
        elif not self._state.active and self._pulse_timer_id:
            GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = 0
            self._pulse_phase = 0.0

    def _pulse_tick(self) -> bool:
        self._pulse_phase = (
            self._pulse_phase + _PULSE_INTERVAL_MS / _PULSE_PERIOD_MS
        ) % 1.0
        # Repaint the icon only; skip tooltip/menu state churn between probes.
        self.item.icon = self.create_icon(size=self._icon_size)
        if self._notify:
            self._notify()
        return True

    def _helper_available(self) -> bool:
        return shutil.which(_PKEXEC_BIN) is not None and (
            shutil.which(_HELPER_BIN) is not None or _SOURCE_HELPER.exists()
        )

    def _run_helper_action(self, action: str) -> None:
        command = _helper_command(action=action)
        if command is None:
            log.warning("Camera lock helper unavailable")
            return

        thread = threading.Thread(
            target=self._run_helper_command,
            args=(command,),
            daemon=True,
        )
        thread.start()

    def _run_helper_command(self, command: list[str]) -> None:
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError:
            log.exception("Failed to run camera lock helper")
            return

        if completed.returncode != 0:
            log.warning(
                "Camera lock helper exited with status %s",
                completed.returncode,
            )
        GLib.idle_add(self._refresh_once)


def _helper_command(*, action: str) -> list[str] | None:
    pkexec = shutil.which(_PKEXEC_BIN)
    if pkexec is None:
        return None

    helper = shutil.which(_HELPER_BIN)
    if helper is not None:
        return [pkexec, helper, action]

    if _SOURCE_HELPER.exists():
        return [pkexec, sys.executable, str(_SOURCE_HELPER), action]
    return None
