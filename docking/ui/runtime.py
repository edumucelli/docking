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

"""Runtime command surfaces exposed by the dock UI shell to handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.core.theme import Theme
    from docking.ui.dock_window import DockWindow
    from docking.ui.placement import MonitorChoice
    from docking.ui.update_popup import UpdateCheckController


class DockRuntime:
    """Narrow imperative API for subsystems that should not own DockWindow."""

    def __init__(
        self,
        window: DockWindow,
        *,
        update_checker: UpdateCheckController,
    ) -> None:
        self._window = window
        self._update_checker = update_checker

    def menu_popup_opened(self) -> None:
        self._window.interaction.menu_popup_opened()

    def menu_popup_closed(self) -> None:
        self._window.interaction.menu_popup_closed()

    def on_hide_mode_changed(self) -> None:
        self._window.on_hide_mode_changed()

    def get_monitor_menu_choices(self) -> list[tuple[str, int]]:
        return self._window.placement.get_monitor_menu_choices()

    def get_monitor_choices(self) -> list[MonitorChoice]:
        return self._window.placement.get_monitor_choices()

    def current_monitor_choice(self) -> int:
        return self._window.placement.current_monitor_choice()

    def primary_monitor_index(self) -> int:
        return self._window.placement.primary_monitor_index()

    def reposition(self) -> None:
        self._window.placement.reposition()

    def set_active_display(self, enabled: bool) -> None:
        if enabled:
            self._window.placement.start_active_display()
        else:
            self._window.placement.stop_active_display()

    def refresh_pressure_handler(self) -> None:
        self._window.placement.refresh_pressure_handler()

    def queue_draw(self) -> None:
        self._window.queue_redraw()

    def set_current_workspace_only(self, enabled: bool) -> None:
        self._window.surface_service.set_workspace_scope(current_workspace_only=enabled)
        self._window.queue_redraw()

    def hide_tooltip(self) -> None:
        self._window.tooltip.hide()

    def hide_hover_ui(self) -> None:
        self._window.tooltip.hide()
        self._window.preview.hide()

    def set_theme(self, theme: Theme) -> None:
        self._window.set_theme(theme)

    def check_for_updates_now(self) -> None:
        self._update_checker.check_now()

    def open_releases_page(self) -> None:
        self._update_checker.open_releases_page()
