"""Bind deliberate dock reveal and recover to auto-hide on registration failure."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import GLib

from docking.core.config import HideMode
from docking.i18n import _
from docking.platform.shortcuts import ShortcutController
from docking.search.services.global_shortcuts import (
    TOGGLE_DOCK_SHORTCUT_ID,
    GlobalShortcutActivation,
    GlobalShortcutsState,
)
from docking.search.services.platform import SearchIntegration

if TYPE_CHECKING:
    from docking.ui.dock_window import DockWindow


class DockShortcutController(ShortcutController):
    shortcut_id = TOGGLE_DOCK_SHORTCUT_ID
    shortcut_description = _("Show or hide Docking")

    def __init__(self, window: DockWindow, integration: SearchIntegration) -> None:
        self._window = window
        self._config = window.config
        self._requested_mode = False
        self._watch_source = 0
        self._init_shortcuts(integration, GLib.idle_add, GLib.source_remove)
        self.add_shortcut_status_listener(self._apply_availability)

    def _current_shortcut_preferences(self) -> tuple[bool, str]:
        return (
            bool(self._config.dock_shortcut)
            and (
                self._requested_mode
                or self._config.hide_mode == HideMode.SHORTCUT_ONLY.value
            ),
            self._config.dock_shortcut,
        )

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self._current_shortcut_preferences()[0]:
            self._start_shortcut_services()

    def _start_shortcut_fallback(self) -> None:
        super()._start_shortcut_fallback()
        if (
            self._shortcut_fallback is not None
            and self._shortcut_fallback.active
            and not self._watch_source
        ):
            self._watch_source = GLib.timeout_add(500, self._check_fallback)

    def stop(self) -> None:
        self._started = False
        self._shortcut_suspended = True
        self._stop_shortcut_services()
        if self._watch_source:
            GLib.source_remove(self._watch_source)
            self._watch_source = 0

    def request_mode(self) -> None:
        self._requested_mode = True
        self.refresh_settings()

    @property
    def pending_mode(self) -> bool:
        return self._requested_mode

    def refresh_settings(self) -> None:
        preferences = self._current_shortcut_preferences()
        if preferences != self._shortcut_preferences:
            self._stop_shortcut_services()
            self._global_shortcuts = self._new_shortcut_service()
            self._shortcut_fallback = self._new_shortcut_fallback()
            self._shortcut_preferences = preferences
            if self._started and preferences[0] and not self._shortcut_suspended:
                self._start_shortcut_services()
        self._notify_shortcut_status()

    def mode_changed(self) -> None:
        self._requested_mode = False
        self.refresh_settings()

    def _on_global_shortcut(self, activation: GlobalShortcutActivation) -> None:
        if self._started and not self._shortcut_suspended:
            self._window.autohide.toggle_shortcut()
            self._window.queue_redraw()

    def _apply_availability(self) -> None:
        if not self._started or self._shortcut_suspended:
            return
        active = self.shortcut_active
        if active and self._requested_mode:
            self._requested_mode = False
            self._config.hide_mode = HideMode.SHORTCUT_ONLY.value
            self._config.save()
        before = self._window.autohide.shortcut_only
        self._window.autohide.set_shortcut_available(active)
        if before != self._window.autohide.shortcut_only:
            self._window.on_hide_mode_changed()

    def _check_fallback(self) -> bool:
        if self._shortcut_fallback is not None and not self._shortcut_suspended:
            self._notify_shortcut_status()
        if self._shortcut_fallback is None or not self._shortcut_fallback.active:
            self._watch_source = 0
            return False
        return True

    def status_text(self) -> str:
        """Explain registration failures without repeating the captured shortcut."""
        if self.shortcut_active:
            return ""
        if not self._config.dock_shortcut:
            if self._config.hide_mode != HideMode.SHORTCUT_ONLY.value:
                return ""
            detail = _("Assign a shortcut before enabling this mode.")
        else:
            if self._shortcut_status is None or self._shortcut_status.state in {
                GlobalShortcutsState.STOPPED,
                GlobalShortcutsState.STARTING,
                GlobalShortcutsState.CREATING,
                GlobalShortcutsState.BINDING,
            }:
                return ""
            detail = self.shortcut_status_text()
        if (
            self._shortcut_status
            and self._shortcut_status.binding
            and not self._shortcut_status.binding.trigger_description
        ):
            detail = _("The desktop has not assigned a shortcut.")
        if (
            self._config.hide_mode == HideMode.SHORTCUT_ONLY.value
            and not self.shortcut_active
        ):
            return _("Using Auto-hide: {reason}").format(reason=detail)
        if self.pending_mode:
            return _("Mode not enabled: {reason}").format(reason=detail)
        return detail
