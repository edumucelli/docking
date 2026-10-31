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

"""GTK lifecycle and menu wiring for Power Profiles applet.

The applet is intentionally thin and delegates backend complexity to
``state.py``. This keeps UI behavior stable regardless of which backend is
active (PPD/tuned/TLP/null):

- icon rendering is driven by a single canonical state snapshot
- menu options are generated from canonical profile IDs
- click behavior cycles through currently available profiles
- all backend calls run in worker threads to keep UI responsive

Concurrency model
=================

``_set_in_progress`` ensures profile-change requests are serialized. This
prevents overlapping backend writes when users click/toggle rapidly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

from docking.applets.platform import AppletPlatformServices

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from docking.applets.base import PlatformServicesApplet
from docking.applets.menu import disabled_menu_item, menu_sections, radio_menu_items
from docking.applets.powerprofiles import meta
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

from .backend import PowerProfilesControlBackend
from .render import create_power_profiles_icon
from .state import (
    PowerProfilesState,
    order_profiles,
    profile_label,
    tooltip_text,
    unavailable_state,
)

if TYPE_CHECKING:
    from docking.core.config import Config

POLL_INTERVAL_S = 5
log = with_context(get_logger(name="powerprofiles"), applet_id=meta.id)


class PowerProfilesApplet(PlatformServicesApplet):
    """Quick selector for power profile backends (PPD/tuned/TLP)."""

    id = meta.id
    name = _("Power Profiles")
    icon_name = "battery-good-symbolic"

    def __init__(
        self,
        icon_size: int,
        config: Config,
        *,
        platform_services: AppletPlatformServices,
    ) -> None:
        self._platform = platform_services
        self._native = platform_services.backends.powerprofiles()
        # Backend is auto-detected once during applet initialization.
        # Polling then queries the same backend instance repeatedly.
        self._backend: PowerProfilesControlBackend = self._native.detect_backend()
        self._state: PowerProfilesState = unavailable_state()
        self._poll_id: int = 0
        self._set_in_progress = False
        self._setting_profile: str | None = None
        self._pending_profile: str | None = None
        self._poll_generation = 0
        self._action_error = ""
        self._worker = BackgroundWorker()
        self._state = self._backend.get_state()
        super().__init__(
            icon_size=icon_size, config=config, platform_services=platform_services
        )
        self.present()

    def create_icon(self, size: int):
        """Render icon from the current canonical profile state."""
        return create_power_profiles_icon(
            size=size,
            profile=self._state.active_profile,
            available=self._state.available,
        )

    def refresh_tooltip(self) -> None:
        """Build multi-line tooltip from state and latest action error, if any."""
        text = tooltip_text(self._state)
        if self._action_error:
            text = f"{text}\n{self._action_error}"
        self.item.name = text

    def start(self, notify: Callable[[], None]) -> None:
        """Start periodic backend polling."""
        super().start(notify=notify)
        self._poll_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        """Stop periodic polling timer."""
        self._poll_generation += 1
        self._pending_profile = None
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0
        super().stop()

    def on_clicked(self) -> None:
        """Cycle to next available profile on left click."""
        if not self._state.available:
            return
        profiles = self._ordered_profiles()
        if len(profiles) < 2:
            return
        try:
            current_index = profiles.index(
                self._pending_profile
                or self._setting_profile
                or self._state.active_profile
            )
        except ValueError as exc:
            log.debug(
                "Active profile %r not found in ordered profile list: %s",
                self._state.active_profile,
                exc,
            )
            current_index = -1
        target = profiles[(current_index + 1) % len(profiles)]
        self._set_profile_async(profile=target)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Build context menu with radio-selector profile entries."""
        if not self._state.available:
            return [disabled_menu_item(_("Power Profiles unavailable"), gtk=Gtk)]

        display = [
            disabled_menu_item(_("Select Profile"), gtk=Gtk),
            *radio_menu_items(
                choices=tuple(
                    (profile_label(profile), profile)
                    for profile in self._ordered_profiles()
                ),
                active_value=self._state.active_profile,
                on_selected=lambda widget, value: self._on_profile_toggled(
                    widget,
                    value,
                ),
                gtk=Gtk,
            ),
        ]

        status: list[Gtk.MenuItem] = []
        if self._state.degraded_reason:
            status.append(
                disabled_menu_item(
                    _("Limited: {reason}").format(reason=self._state.degraded_reason),
                    gtk=Gtk,
                )
            )

        return menu_sections(status=status, display=display, gtk=Gtk)

    def _ordered_profiles(self) -> tuple[str, ...]:
        """Resolve safe ordered profile list for menu/cycle actions."""
        if self._state.profiles:
            return order_profiles(self._state.profiles)
        if self._state.active_profile:
            return (self._state.active_profile,)
        return ("balanced", "performance", "power-saver")

    def _tick(self) -> bool:
        """Periodic GLib timer callback; delegates backend call to a thread."""
        if self._set_in_progress:
            return True
        generation = self._poll_generation
        self._worker.run_guarded(
            key="poll",
            name="powerprofiles-poll",
            fn=self._poll_worker,
            on_result=lambda state: self._on_poll_result(state, generation=generation),
        )
        return True

    def _poll_worker(self) -> PowerProfilesState:
        """Background thread: fetch backend state, then hand off to GTK loop."""
        return self._backend.get_state()

    def _on_poll_result(
        self, state: PowerProfilesState, *, generation: int | None = None
    ) -> bool:
        """GTK-thread state update from periodic polling."""
        if self._set_in_progress or (
            generation is not None and generation != self._poll_generation
        ):
            return False
        changed = state != self._state
        self._state = state
        if changed:
            self.present()
        return False

    def _on_profile_toggled(self, widget: Gtk.RadioMenuItem, profile: str) -> None:
        """Radio-item callback; dispatch profile change only on active toggle."""
        if not widget.get_active():
            return
        if profile == (
            self._pending_profile or self._setting_profile or self._state.active_profile
        ):
            return
        self._set_profile_async(profile=profile)

    def _set_profile_async(self, *, profile: str) -> None:
        """Run backend profile switch on worker thread."""
        self._poll_generation += 1
        if self._set_in_progress:
            self._pending_profile = profile
            return
        self._set_in_progress = True
        self._setting_profile = profile

        def task() -> tuple[bool, PowerProfilesState]:
            return (
                self._backend.set_active_profile(profile),
                self._backend.get_state(),
            )

        self._worker.run(
            name="powerprofiles-set",
            fn=task,
            on_result=lambda result: self._on_set_result(profile, result[0], result[1]),
            on_error=lambda exc: self._on_set_error(profile, exc),
        )

    def _on_set_result(
        self,
        profile: str,
        success: bool,
        state: PowerProfilesState,
    ) -> bool:
        """GTK-thread handler for async profile-set completion."""
        self._set_in_progress = False
        self._setting_profile = None
        self._state = state
        if success:
            self._action_error = ""
        else:
            self._action_error = f"Failed to set {profile_label(profile)}"
        self.present()
        self._start_pending_profile()
        return False

    def _on_set_error(self, profile: str, _exc: Exception) -> bool:
        self._set_in_progress = False
        self._setting_profile = None
        self._action_error = f"Failed to set {profile_label(profile)}"
        self.present()
        self._start_pending_profile()
        return False

    def _start_pending_profile(self) -> None:
        profile = self._pending_profile
        self._pending_profile = None
        if profile is not None and profile != self._state.active_profile:
            self._set_profile_async(profile=profile)
