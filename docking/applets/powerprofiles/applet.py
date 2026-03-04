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

import threading
from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from docking.applets.base import Applet
from docking.applets.identity import AppletId

from .render import create_power_profiles_icon
from .state import (
    PowerProfilesControlBackend,
    PowerProfilesState,
    detect_backend,
    order_profiles,
    profile_label,
    tooltip_text,
    unavailable_state,
)

if TYPE_CHECKING:
    from docking.core.config import Config

POLL_INTERVAL_S = 5


class PowerProfilesApplet(Applet):
    """Quick selector for power profile backends (PPD/tuned/TLP)."""

    id = AppletId.POWERPROFILES
    name = "Power Profiles"
    icon_name = "battery-good-symbolic"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        # Backend is auto-detected once during applet initialization.
        # Polling then queries the same backend instance repeatedly.
        self._backend: PowerProfilesControlBackend = detect_backend()
        self._state: PowerProfilesState = unavailable_state()
        self._poll_id: int = 0
        self._set_in_progress = False
        self._action_error = ""
        self._state = self._backend.get_state()
        super().__init__(icon_size=icon_size, config=config)

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
            current_index = profiles.index(self._state.active_profile)
        except ValueError:
            current_index = -1
        target = profiles[(current_index + 1) % len(profiles)]
        self._set_profile_async(profile=target)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Build context menu with radio-selector profile entries."""
        if not self._state.available:
            item = Gtk.MenuItem(label="Power Profiles unavailable")
            item.set_sensitive(False)
            return [item]

        items: list[Gtk.MenuItem] = []
        title = Gtk.MenuItem(label="Select Profile")
        title.set_sensitive(False)
        items.append(title)

        group_head: Gtk.RadioMenuItem | None = None
        for profile in self._ordered_profiles():
            label = profile_label(profile)
            item = Gtk.RadioMenuItem(label=label)
            if group_head is None:
                group_head = item
            else:
                item.join_group(group_head)
            item.set_active(profile == self._state.active_profile)
            item.connect("toggled", self._on_profile_toggled, profile)
            items.append(item)

        if self._state.degraded_reason:
            items.append(Gtk.SeparatorMenuItem())
            reason = Gtk.MenuItem(label=f"Limited: {self._state.degraded_reason}")
            reason.set_sensitive(False)
            items.append(reason)

        return items

    def _ordered_profiles(self) -> tuple[str, ...]:
        """Resolve safe ordered profile list for menu/cycle actions."""
        if self._state.profiles:
            return order_profiles(self._state.profiles)
        if self._state.active_profile:
            return (self._state.active_profile,)
        return ("balanced", "performance", "power-saver")

    def _tick(self) -> bool:
        """Periodic GLib timer callback; delegates backend call to a thread."""
        threading.Thread(target=self._poll_worker, daemon=True).start()
        return True

    def _poll_worker(self) -> None:
        """Background thread: fetch backend state, then hand off to GTK loop."""
        state = self._backend.get_state()
        GLib.idle_add(self._on_poll_result, state)

    def _on_poll_result(self, state: PowerProfilesState) -> bool:
        """GTK-thread state update from periodic polling."""
        changed = state != self._state
        self._state = state
        if changed:
            self.refresh_presentation()
        return False

    def _on_profile_toggled(self, widget: Gtk.RadioMenuItem, profile: str) -> None:
        """Radio-item callback; dispatch profile change only on active toggle."""
        if not widget.get_active():
            return
        if profile == self._state.active_profile:
            return
        self._set_profile_async(profile=profile)

    def _set_profile_async(self, *, profile: str) -> None:
        """Run backend profile switch on worker thread."""
        if self._set_in_progress:
            return
        self._set_in_progress = True

        def worker() -> None:
            success = self._backend.set_active_profile(profile)
            state = self._backend.get_state()
            GLib.idle_add(self._on_set_result, profile, success, state)

        threading.Thread(target=worker, daemon=True).start()

    def _on_set_result(
        self,
        profile: str,
        success: bool,
        state: PowerProfilesState,
    ) -> bool:
        """GTK-thread handler for async profile-set completion."""
        self._set_in_progress = False
        self._state = state
        if success:
            self._action_error = ""
        else:
            self._action_error = f"Failed to set {profile_label(profile)}"
        self.refresh_presentation()
        return False
