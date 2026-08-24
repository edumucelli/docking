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

"""Preferences window controller for Docking.

This module owns the dock settings window opened from the dock background menu.
The design is intentionally similar to the About controller: one controller
owns one top-level window and reuses it while it stays open.

The UI shape is inspired by Plank's preferences window:

    +----------------------------------------------+
    | Preferences                                  |
    |                                              |
    |  [ Appearance ] [ Behavior ] [ Applets ]     |
    |  [ Updates ]                                 |
    |                                              |
    |  controls for the active tab...              |
    |  or                                          |
    |  applet enable/disable cards...              |
    +----------------------------------------------+

The dock already exposed many of these actions via the context menu. This
window does not invent a second configuration model; it gives those same
settings a persistent, easier-to-scan home.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GLib, Gtk

from docking.applets import get_applet_catalog
from docking.applets.base import load_catalog_icon
from docking.applets.identity import (
    APPLET_CATEGORY_ORDER,
    AppletCategory,
    AppletMeta,
    applet_desktop_id,
)
from docking.applets.identity import is_applet_desktop_id as is_applet
from docking.applets.separator import meta as _separator_meta
from docking.core.config import (
    MAX_ADDITIONAL_DISTANCE_FROM_EDGE,
    MAX_ICON_SIZE,
    MAX_PRESSURE_THRESHOLD,
    MAX_RECENT_APPS_RETENTION_DAYS,
    MAX_TRANSPARENCY,
    MAX_ZOOM_PERCENT,
    MIN_ADDITIONAL_DISTANCE_FROM_EDGE,
    MIN_ICON_SIZE,
    MIN_PRESSURE_THRESHOLD,
    MIN_RECENT_APPS_RETENTION_DAYS,
    MIN_TRANSPARENCY,
    MIN_ZOOM_PERCENT,
    LeftClickAction,
    MiddleClickAction,
    StackUnfold,
    WindowListSort,
)
from docking.core.position import Position
from docking.core.theme import Theme, list_theme_names
from docking.core.updates import load_state
from docking.i18n import _
from docking.log import get_logger
from docking.search.config import (
    DEFAULT_GLOBAL_SEARCH_WEB_ENGINE,
    GLOBAL_SEARCH_WEB_ENGINES,
)
from docking.search.ui.shortcut_capture import ShortcutCaptureButton

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.model import DockModel
    from docking.search.controller import GlobalSearchController
    from docking.ui.dnd import DnDHandler
    from docking.ui.placement import MonitorChoice
    from docking.ui.runtime import DockRuntime


APPLET_LIST_ICON_PX = 32
APPLET_GRID_COLUMNS = 3
APPLET_CELL_WIDTH_PX = 168
APPEARANCE_ROW_WIDTH_PX = 428
PREFERENCES_WINDOW_WIDTH_PX = 460
PREFERENCES_WINDOW_HEIGHT_PX = 620
PREFERENCES_WINDOW_MIN_HEIGHT_PX = 240
PREFERENCES_WINDOW_SCREEN_MARGIN_PX = 72
WINDOW_OUTER_SPACING_PX = 8
WINDOW_OUTER_BORDER_PX = 12
APPEARANCE_TAB_SPACING_PX = 12
APPEARANCE_TAB_BORDER_PX = 16
APPLET_TAB_SPACING_PX = 10
APPLET_TAB_BORDER_PX = 16
SECTION_SPACING_PX = 8
SECTION_CONTENT_INSET_PX = 18
SECTION_HEADER_TOP_MARGIN_PX = 6
SECTION_HEADER_BOTTOM_MARGIN_PX = 2
ROW_SPACING_PX = 12
HIDE_MODE_COMBO_WIDTH_PX = 180
HIDE_MODE_INFO_ICON_WIDTH_PX = 14
TRANSPARENCY_SCALE_WIDTH_PX = 132
HIDE_MODE_BOX_SPACING_PX = 4
APPLET_GRID_COLUMN_SPACING_PX = 16
APPLET_GRID_ROW_SPACING_PX = 8
APPLET_ROW_CONTENT_SPACING_PX = 6
ZOOM_PERCENT_SCALE = 100
ZOOM_PERCENT_STEP = 5
TRANSPARENCY_PERCENT_SCALE = 100
TRANSPARENCY_PERCENT_STEP = 5
HIDE_DELAY_MAX_MS = 5000
HIDE_DELAY_STEP_MS = 50
INFO_POPOVER_PADDING_PX = 8
log = get_logger("settings")

SettingsRow = tuple[str, Gtk.Widget, str | None]
ConfigScalar = bool | int | float | str


@dataclass
class _ScalarBinding:
    """Bind one scalar config attribute to one widget."""

    config_attr: str
    widget: Gtk.Widget
    read_widget: Callable[[], ConfigScalar | None]
    write_widget: Callable[[ConfigScalar], None]
    on_change: Callable[[], None] | None = None


class SettingsActions:
    """Facade for side effects triggered by the preferences window.

    The settings controller owns widgets and config synchronization, not the
    dock shell. This object keeps those concerns separate while avoiding a bag
    of callbacks: every method is a named action the preferences UI is allowed
    to request from runtime collaborators.
    """

    def __init__(
        self,
        *,
        runtime: DockRuntime,
        dnd: DnDHandler,
        model: DockModel,
        search: GlobalSearchController,
    ) -> None:
        self._runtime = runtime
        self._dnd = dnd
        self._model = model
        self._search = search

    def on_hide_mode_changed(self) -> None:
        self._runtime.on_hide_mode_changed()

    def get_monitor_choices(self) -> list[MonitorChoice]:
        return self._runtime.get_monitor_choices()

    def current_monitor_choice(self) -> int:
        return self._runtime.current_monitor_choice()

    def reposition(self) -> None:
        self._runtime.reposition()

    def set_active_display(self, enabled: bool) -> None:
        self._runtime.set_active_display(enabled)

    def refresh_pressure_handler(self) -> None:
        self._runtime.refresh_pressure_handler()

    def set_icons_locked(self, locked: bool) -> None:
        self._dnd.set_locked(locked)

    def queue_draw(self) -> None:
        self._runtime.queue_draw()

    def refresh_launcher_overlay_visibility(self) -> None:
        self._model.refresh_launcher_overlay_visibility()

    def set_current_workspace_only(self, enabled: bool) -> None:
        self._runtime.set_current_workspace_only(enabled)

    def hide_tooltip(self) -> None:
        self._runtime.hide_tooltip()

    def set_theme(self, theme: Theme) -> None:
        self._runtime.set_theme(theme)

    def check_for_updates_now(self) -> None:
        self._runtime.check_for_updates_now()

    def open_releases_page(self) -> None:
        self._runtime.open_releases_page()

    def refresh_search_settings(self) -> None:
        self._search.refresh_settings()

    def suspend_search_shortcuts(self) -> None:
        self._search.suspend_shortcuts()

    def resume_search_shortcuts(self) -> None:
        self._search.resume_shortcuts()

    def search_shortcut_status(self) -> str:
        return self._search.shortcut_status_text()

    def search_shortcut_status_summary(self) -> str:
        return self._search.shortcut_status_summary()

    def add_search_shortcut_status_listener(
        self,
        listener: Callable[[], None],
    ) -> Callable[[], None]:
        return self._search.add_shortcut_status_listener(listener)


class SettingsWindowController:
    """Owns the dock preferences window lifecycle and widget synchronization."""

    def __init__(
        self,
        *,
        parent: Gtk.Window,
        actions: SettingsActions,
        model: DockModel,
        config: Config,
    ) -> None:
        self._parent = parent
        self._actions = actions
        self._model = model
        self._config = config
        self._window: Gtk.Window | None = None
        self._syncing_widgets = False
        self._unsubscribe_search_shortcut_status: Callable[[], None] | None = (
            self._actions.add_search_shortcut_status_listener(
                self._update_search_shortcut_status
            )
        )

        self._hide_mode_combo: Any = None
        self._hide_mode_info: Any = None
        self._left_click_combo: Any = None
        self._middle_click_combo: Any = None
        self._stack_unfold_combo: Any = None
        self._window_list_sort_combo: Any = None
        self._window_count_numbers_switch: Any = None
        self._launcher_badges_switch: Any = None
        self._launcher_progress_switch: Any = None
        self._previews_switch: Any = None
        self._tooltips_switch: Any = None
        self._lock_icons_switch: Any = None
        self._workspace_only_switch: Any = None
        self._active_display_switch: Any = None
        self._monitor_combo: Any = None
        self._monitor_info: Any = None
        self._anchor_applets_switch: Any = None
        self._anchor_files_switch: Any = None
        self._zoom_enabled_switch: Any = None
        self._theme_combo: Any = None
        self._position_combo: Any = None
        self._icon_size_spin: Any = None
        self._transparency_scale: Any = None
        self._additional_distance_scale: Any = None
        self._additional_distance_info: Any = None
        self._pressure_reveal_switch: Any = None
        self._pressure_threshold_scale: Any = None
        self._pressure_threshold_info: Any = None
        self._zoom_percent_spin: Any = None
        self._hide_delay_spin: Any = None
        self._unhide_delay_spin: Any = None
        self._update_check_switch: Any = None
        self._startup_tips_switch: Any = None
        self._update_interval_combo: Any = None
        self._update_status_label: Any = None
        self._recent_apps_switch: Any = None
        self._recent_apps_max_spin: Any = None
        self._recent_apps_retention_spin: Any = None
        self._recent_docs_switch: Any = None
        self._recent_docs_max_spin: Any = None
        self._global_search_switch: Any = None
        self._global_search_shortcut_box: Any = None
        self._global_search_shortcut_entry: Any = None
        self._global_search_web_engine_combo: Any = None
        self._global_search_status_label: Any = None
        self._applets_box: Any = None
        self._applet_checks: dict[str, Gtk.CheckButton] = {}
        self._bindings: list[_ScalarBinding] = []

    def show(self) -> None:
        """Show the preferences window, creating it on first use."""
        if self._window is None:
            self._window = self._build_window()
        self._sync_widgets()
        self._rebuild_applet_tab()
        self._window.show_all()
        self._window.present()

    def close(self) -> None:
        """Release the status subscription and destroy the preferences window."""
        unsubscribe = self._unsubscribe_search_shortcut_status
        self._unsubscribe_search_shortcut_status = None
        window = self._window
        self._window = None
        try:
            if unsubscribe is not None:
                unsubscribe()
        finally:
            if window is not None:
                window.destroy()

    def _build_window(self) -> Gtk.Window:
        window = Gtk.Window(
            title=_("Preferences"),
            transient_for=self._parent,
            destroy_with_parent=True,
        )
        window.set_default_size(
            PREFERENCES_WINDOW_WIDTH_PX,
            self._preferences_default_height(),
        )
        window.set_modal(False)
        window.set_resizable(True)
        window.set_position(Gtk.WindowPosition.CENTER)
        window.connect("destroy", self._on_destroy)

        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=WINDOW_OUTER_SPACING_PX,
        )
        outer.set_border_width(WINDOW_OUTER_BORDER_PX)

        stack = Gtk.Stack()
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(stack)
        switcher.set_halign(Gtk.Align.CENTER)

        stack.add_titled(self._build_appearance_tab(), "appearance", _("Appearance"))
        stack.add_titled(self._build_behavior_tab(), "behavior", _("Behavior"))
        stack.add_titled(self._build_applets_tab(), "applets", _("Applets"))
        stack.add_titled(self._build_updates_tab(), "updates", _("Updates"))

        outer.pack_start(switcher, False, False, 0)
        outer.pack_start(stack, True, True, 0)
        window.add(outer)
        return window

    def _build_appearance_tab(self) -> Gtk.Widget:
        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=APPEARANCE_TAB_SPACING_PX,
        )
        outer.set_border_width(APPEARANCE_TAB_BORDER_PX)
        self._bindings.clear()

        self._hide_mode_combo = Gtk.ComboBoxText()
        self._hide_mode_combo.set_size_request(
            HIDE_MODE_COMBO_WIDTH_PX
            - HIDE_MODE_INFO_ICON_WIDTH_PX
            - HIDE_MODE_BOX_SPACING_PX,
            -1,
        )
        for mode_value, mode_label in [
            ("none", _("Don't Hide")),
            ("always-on-top", _("Always on Top")),
            ("autohide", _("Auto-hide")),
            ("intelligent", _("Intelligent")),
            ("dodge-active", _("Dodge Active")),
            ("window-dodge", _("Dodge Windows")),
            ("dodge-maximized", _("Dodge Maximized")),
        ]:
            self._hide_mode_combo.append(mode_value, mode_label)

        self._hide_mode_info = self._new_info_icon()
        self._hide_mode_combo.connect("changed", self._on_hide_mode_combo_changed)
        self._update_hide_mode_description()

        self._left_click_combo = Gtk.ComboBoxText()
        self._left_click_combo.set_size_request(HIDE_MODE_COMBO_WIDTH_PX, -1)
        for action_value, action_label in [
            (LeftClickAction.TOGGLE.value, _("Toggle Focus")),
            (LeftClickAction.CYCLE.value, _("Cycle Windows")),
            (LeftClickAction.MOST_RECENT.value, _("Most Recent Window")),
        ]:
            self._left_click_combo.append(action_value, action_label)

        self._middle_click_combo = Gtk.ComboBoxText()
        self._middle_click_combo.set_size_request(HIDE_MODE_COMBO_WIDTH_PX, -1)
        for action_value, action_label in [
            (MiddleClickAction.NEW_WINDOW.value, _("New Window")),
            (MiddleClickAction.MINIMIZE.value, _("Minimize Windows")),
            (MiddleClickAction.CLOSE_FOCUSED.value, _("Close Focused Window")),
        ]:
            self._middle_click_combo.append(action_value, action_label)

        self._stack_unfold_combo = Gtk.ComboBoxText()
        self._stack_unfold_combo.set_size_request(HIDE_MODE_COMBO_WIDTH_PX, -1)
        for mode_value, mode_label in [
            (StackUnfold.CLICK.value, _("Click")),
            (StackUnfold.HOVER.value, _("Hover")),
        ]:
            self._stack_unfold_combo.append(mode_value, mode_label)

        self._window_list_sort_combo = Gtk.ComboBoxText()
        self._window_list_sort_combo.set_size_request(HIDE_MODE_COMBO_WIDTH_PX, -1)
        for sort_value, sort_label in [
            (WindowListSort.DEFAULT.value, _("Default")),
            (WindowListSort.ALPHABETICAL.value, _("Alphabetical")),
        ]:
            self._window_list_sort_combo.append(sort_value, sort_label)

        self._previews_switch = self._new_switch()
        self._tooltips_switch = self._new_switch()
        self._window_count_numbers_switch = self._new_switch()
        self._launcher_badges_switch = self._new_switch()
        self._launcher_progress_switch = self._new_switch()
        self._lock_icons_switch = self._new_switch()
        self._workspace_only_switch = self._new_switch()
        self._active_display_switch = self._new_switch()
        self._anchor_applets_switch = self._new_switch()
        self._anchor_files_switch = self._new_switch()
        self._zoom_enabled_switch = self._new_switch()
        self._update_check_switch = self._new_switch()
        self._startup_tips_switch = self._new_switch()

        self._update_interval_combo = Gtk.ComboBoxText()
        for value, label in [
            ("24", _("Daily")),
            ("168", _("Weekly")),
        ]:
            self._update_interval_combo.append(value, label)

        self._update_status_label = Gtk.Label()
        self._update_status_label.set_xalign(0.0)
        self._update_status_label.set_line_wrap(True)

        self._theme_combo = Gtk.ComboBoxText()
        for theme_name in list_theme_names():
            self._theme_combo.append(theme_name, theme_name.replace("-", " ").title())

        self._position_combo = Gtk.ComboBoxText()
        for pos in Position:
            self._position_combo.append(pos.value, pos.value.capitalize())

        self._monitor_combo = Gtk.ComboBoxText()
        self._monitor_combo.set_size_request(HIDE_MODE_COMBO_WIDTH_PX, -1)
        self._monitor_combo.connect("changed", self._on_monitor_combo_changed)
        self._monitor_info = self._new_info_icon(
            _(
                "When Follow Cursor is enabled, the dock follows the pointer "
                "across monitors. The selected monitor is kept as the fallback."
            )
        )
        monitor_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=HIDE_MODE_BOX_SPACING_PX,
        )
        monitor_box.set_size_request(TRANSPARENCY_SCALE_WIDTH_PX, -1)
        monitor_box.pack_start(self._monitor_combo, False, False, 0)
        monitor_box.pack_start(self._monitor_info, False, False, 0)

        self._icon_size_spin = self._new_numeric_spin_button(
            minimum=MIN_ICON_SIZE,
            maximum=MAX_ICON_SIZE,
            step=1,
        )
        self._transparency_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            int(MIN_TRANSPARENCY * TRANSPARENCY_PERCENT_SCALE),
            int(MAX_TRANSPARENCY * TRANSPARENCY_PERCENT_SCALE),
            TRANSPARENCY_PERCENT_STEP,
        )
        self._transparency_scale.set_digits(0)
        self._transparency_scale.set_draw_value(True)
        self._transparency_scale.set_size_request(TRANSPARENCY_SCALE_WIDTH_PX, -1)
        transparency_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        transparency_box.set_size_request(TRANSPARENCY_SCALE_WIDTH_PX, -1)
        transparency_box.pack_end(self._transparency_scale, False, False, 0)
        self._zoom_percent_spin = self._new_numeric_spin_button(
            minimum=int(MIN_ZOOM_PERCENT * ZOOM_PERCENT_SCALE),
            maximum=int(MAX_ZOOM_PERCENT * ZOOM_PERCENT_SCALE),
            step=ZOOM_PERCENT_STEP,
        )
        self._additional_distance_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            MIN_ADDITIONAL_DISTANCE_FROM_EDGE,
            MAX_ADDITIONAL_DISTANCE_FROM_EDGE,
            1,
        )
        self._additional_distance_scale.set_digits(0)
        self._additional_distance_scale.set_draw_value(True)
        self._additional_distance_scale.set_size_request(
            TRANSPARENCY_SCALE_WIDTH_PX, -1
        )
        self._additional_distance_info = self._new_info_icon(
            _("Added on top of the theme's own distance from the edge.")
        )
        additional_distance_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=HIDE_MODE_BOX_SPACING_PX,
        )
        additional_distance_box.set_size_request(TRANSPARENCY_SCALE_WIDTH_PX, -1)
        additional_distance_box.pack_start(
            self._additional_distance_scale, False, False, 0
        )
        additional_distance_box.pack_start(
            self._additional_distance_info, False, False, 0
        )
        self._hide_delay_spin = self._new_numeric_spin_button(
            minimum=0,
            maximum=HIDE_DELAY_MAX_MS,
            step=HIDE_DELAY_STEP_MS,
        )
        self._unhide_delay_spin = self._new_numeric_spin_button(
            minimum=0,
            maximum=HIDE_DELAY_MAX_MS,
            step=HIDE_DELAY_STEP_MS,
        )
        self._pressure_reveal_switch = self._new_switch()
        self._pressure_threshold_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL,
            MIN_PRESSURE_THRESHOLD,
            MAX_PRESSURE_THRESHOLD,
            1,
        )
        self._pressure_threshold_scale.set_digits(0)
        self._pressure_threshold_scale.set_draw_value(True)
        self._pressure_threshold_scale.set_size_request(
            TRANSPARENCY_SCALE_WIDTH_PX
            - HIDE_MODE_INFO_ICON_WIDTH_PX
            - HIDE_MODE_BOX_SPACING_PX,
            -1,
        )
        self._pressure_threshold_info = self._new_info_icon(
            _(
                "Pixels of cursor pressure against the edge required to "
                "reveal a hidden dock. Higher values mean the dock will not "
                "reveal as easily."
            )
        )

        self._recent_apps_switch = self._new_switch()
        self._recent_apps_max_spin = self._new_numeric_spin_button(
            minimum=1, maximum=15, step=1
        )
        self._recent_apps_retention_spin = self._new_numeric_spin_button(
            minimum=MIN_RECENT_APPS_RETENTION_DAYS,
            maximum=MAX_RECENT_APPS_RETENTION_DAYS,
            step=1,
        )
        self._recent_docs_switch = self._new_switch()
        self._recent_docs_max_spin = self._new_numeric_spin_button(
            minimum=1, maximum=25, step=1
        )
        self._global_search_switch = self._new_switch()
        self._global_search_shortcut_entry = ShortcutCaptureButton()
        self._global_search_shortcut_entry.connect(
            "capture-started",
            lambda *_: self._actions.suspend_search_shortcuts(),
        )
        self._global_search_shortcut_entry.connect(
            "capture-ended",
            lambda *_: self._actions.resume_search_shortcuts(),
        )
        self._global_search_status_label = Gtk.Label()
        self._global_search_status_label.set_xalign(0.0)
        self._global_search_status_label.set_line_wrap(False)
        self._global_search_status_label.set_max_width_chars(28)
        self._global_search_status_label.set_margin_top(4)
        self._global_search_status_label.get_style_context().add_class("dim-label")
        self._global_search_shortcut_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
        )
        self._global_search_shortcut_box.pack_start(
            self._global_search_shortcut_entry,
            False,
            False,
            0,
        )
        self._global_search_shortcut_box.pack_start(
            self._global_search_status_label,
            False,
            False,
            0,
        )
        self._global_search_web_engine_combo = Gtk.ComboBoxText()
        web_engine_labels = {
            "duckduckgo": "DuckDuckGo",
            "google": "Google",
            "brave": "Brave Search",
            "bing": "Bing",
        }
        for engine_id in GLOBAL_SEARCH_WEB_ENGINES:
            self._global_search_web_engine_combo.append(
                engine_id,
                web_engine_labels.get(engine_id, engine_id.title()),
            )
        self._global_search_web_engine_combo.set_active_id(
            DEFAULT_GLOBAL_SEARCH_WEB_ENGINE
        )
        self._register_bindings()

        self._append_section(
            outer=outer,
            title=_("Look"),
            rows=[
                (_("Theme"), self._theme_combo, _("Choose the dock color palette.")),
                (
                    _("Icon Size"),
                    self._icon_size_spin,
                    _("Set the base size of dock icons before zoom is applied."),
                ),
                (
                    _("Transparency"),
                    transparency_box,
                    _("Adjust how transparent the dock background is."),
                ),
                (
                    _("Zoom"),
                    self._zoom_enabled_switch,
                    _("Enlarge icons when the pointer hovers over the dock."),
                ),
                (
                    _("Zoom Percent"),
                    self._zoom_percent_spin,
                    _("Set how much hovered icons grow when zoom is enabled."),
                ),
                (
                    _("Show Tooltips"),
                    self._tooltips_switch,
                    _("Show item names and details when hovering over dock items."),
                ),
                (
                    _("Window Previews"),
                    self._previews_switch,
                    _("Show window thumbnails when hovering over running apps."),
                ),
                (
                    _("Show Window Counts"),
                    self._window_count_numbers_switch,
                    _(
                        "Show a number on running app indicators when multiple "
                        "windows are open."
                    ),
                ),
                (
                    _("Application Badges"),
                    self._launcher_badges_switch,
                    _(
                        "Show numeric counts reported by applications on their "
                        "dock icons."
                    ),
                ),
                (
                    _("Application Progress"),
                    self._launcher_progress_switch,
                    _(
                        "Show task progress reported by applications on their "
                        "dock icons."
                    ),
                ),
            ],
        )
        self._append_section(
            outer=outer,
            title=_("Placement"),
            rows=[
                (
                    _("Position"),
                    self._position_combo,
                    _("Choose which screen edge the dock uses."),
                ),
                (_("Extra Distance from Edge"), additional_distance_box, None),
                (
                    _("Current Workspace Only"),
                    self._workspace_only_switch,
                    _(
                        "Show running windows only from the active workspace "
                        "when supported."
                    ),
                ),
            ],
        )
        self._append_section(
            outer=outer,
            title=_("Monitor"),
            rows=[
                (
                    _("Follow Cursor"),
                    self._active_display_switch,
                    _(
                        "Move the dock to the monitor where the pointer is "
                        "currently located."
                    ),
                ),
                (_("Monitor"), monitor_box, None),
            ],
        )
        self._append_section(
            outer=outer,
            title=_("Layout"),
            rows=[
                (
                    _("Lock Positions"),
                    self._lock_icons_switch,
                    _(
                        "Prevent dock items from being reordered or removed by "
                        "drag and drop."
                    ),
                ),
                (
                    _("Anchor Applets to End"),
                    self._anchor_applets_switch,
                    _("Keep applets grouped at the end of the dock."),
                ),
                (
                    _("Anchor Files to End"),
                    self._anchor_files_switch,
                    _("Keep pinned files and folders grouped at the end of the dock."),
                ),
            ],
        )

        return self._new_tab_scroller(outer, propagate_natural_width=True)

    def _build_behavior_tab(self) -> Gtk.Widget:
        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=APPEARANCE_TAB_SPACING_PX,
        )
        outer.set_border_width(APPEARANCE_TAB_BORDER_PX)

        hide_mode_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=HIDE_MODE_BOX_SPACING_PX,
        )
        hide_mode_box.set_size_request(HIDE_MODE_COMBO_WIDTH_PX, -1)
        hide_mode_box.pack_start(self._hide_mode_combo, True, True, 0)
        hide_mode_box.pack_start(self._hide_mode_info, False, False, 0)

        pressure_threshold_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=HIDE_MODE_BOX_SPACING_PX,
        )
        pressure_threshold_box.set_size_request(TRANSPARENCY_SCALE_WIDTH_PX, -1)
        pressure_threshold_box.pack_start(
            self._pressure_threshold_scale, False, False, 0
        )
        pressure_threshold_box.pack_start(
            self._pressure_threshold_info, False, False, 0
        )

        self._append_section(
            outer=outer,
            title=_("Mouse"),
            rows=[
                (
                    _("Left Click"),
                    self._left_click_combo,
                    _(
                        "Choose what happens when clicking a running app with "
                        "the left mouse button."
                    ),
                ),
                (
                    _("Middle Click"),
                    self._middle_click_combo,
                    _(
                        "Choose what happens when clicking an app with the "
                        "middle mouse button."
                    ),
                ),
                (
                    _("Window List Sort"),
                    self._window_list_sort_combo,
                    _("Choose how open windows are ordered in app context menus."),
                ),
            ],
        )
        self._append_section(
            outer=outer,
            title=_("Behavior"),
            rows=[
                (_("Hide Mode"), hide_mode_box, None),
                (
                    _("Hide Delay"),
                    self._hide_delay_spin,
                    _(
                        "Set how long the dock waits before hiding after the "
                        "pointer leaves."
                    ),
                ),
                (
                    _("Unhide Delay"),
                    self._unhide_delay_spin,
                    _(
                        "Set how long the dock waits before showing after the "
                        "pointer returns."
                    ),
                ),
                (
                    _("Pressure Reveal"),
                    self._pressure_reveal_switch,
                    _(
                        "Require the pointer to push against the screen edge "
                        "before revealing a hidden dock."
                    ),
                ),
                (_("Pressure Threshold"), pressure_threshold_box, None),
                (
                    _("Show Startup Tips"),
                    self._startup_tips_switch,
                    _(
                        "Show one Docking usage tip after startup, unless a "
                        "higher-priority startup notification is visible."
                    ),
                ),
            ],
        )
        self._append_section(
            outer=outer,
            title=_("Stacks"),
            rows=[
                (
                    _("Open On"),
                    self._stack_unfold_combo,
                    _("Choose whether stacks open on click or while hovering."),
                ),
            ],
        )
        self._append_section(
            outer=outer,
            title=_("Recent Apps"),
            rows=[
                (
                    _("Show Recently Used Apps"),
                    self._recent_apps_switch,
                    _(
                        "Show recently closed apps between pinned "
                        "launchers and running apps."
                    ),
                ),
                (
                    _("Number of Recent Apps"),
                    self._recent_apps_max_spin,
                    _("Maximum recently used app icons to display."),
                ),
                (
                    _("Keep Recent Apps For"),
                    self._recent_apps_retention_spin,
                    _("Remove recent apps after this many days of inactivity."),
                ),
            ],
        )
        self._append_section(
            outer=outer,
            title=_("Recent Documents"),
            rows=[
                (
                    _("Show Recent Documents"),
                    self._recent_docs_switch,
                    _(
                        'Show a "Recent Documents" submenu when '
                        "right-clicking an app icon."
                    ),
                ),
                (
                    _("Max Documents Per App"),
                    self._recent_docs_max_spin,
                    _("Maximum recent document entries shown per app."),
                ),
            ],
        )
        self._append_section(
            outer=outer,
            title=_("Global Search"),
            rows=[
                (
                    _("Enabled"),
                    self._global_search_switch,
                    _("Enable Docking's process-wide search palette."),
                ),
                (
                    _("Preferred Shortcut"),
                    self._global_search_shortcut_box,
                    _(
                        "Click the button, then press the desired key sequence. "
                        "The desktop may reserve some shortcuts."
                    ),
                ),
                (
                    _("Search Engine"),
                    self._global_search_web_engine_combo,
                    _("Choose the engine used by web fallback."),
                ),
            ],
        )

        return self._new_tab_scroller(outer, propagate_natural_width=True)

    def _new_info_icon(self, tooltip: str = "") -> Gtk.EventBox:
        icon = Gtk.EventBox()
        icon.set_visible_window(False)
        icon.set_size_request(HIDE_MODE_INFO_ICON_WIDTH_PX, -1)
        icon.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        icon.add(
            Gtk.Image.new_from_icon_name(
                "dialog-information-symbolic",
                Gtk.IconSize.MENU,
            )
        )

        popover = Gtk.Popover.new(icon)
        popover.set_modal(False)
        popover.set_position(Gtk.PositionType.TOP)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_border_width(INFO_POPOVER_PADDING_PX)
        label = Gtk.Label()
        label.set_xalign(0.0)
        label.set_line_wrap(True)
        label.set_max_width_chars(48)
        content.pack_start(label, False, False, 0)
        popover.add(content)

        icon._docking_info_popover = popover
        icon._docking_info_label = label
        icon._docking_info_text = ""
        icon.connect("enter-notify-event", self._on_info_icon_enter)
        icon.connect("leave-notify-event", self._on_info_icon_leave)
        self._set_info_icon_text(icon, tooltip)
        return icon

    def _set_info_icon_text(self, icon: Gtk.Widget, text: str) -> None:
        icon._docking_info_text = text
        icon._docking_info_label.set_label(text)

    def _on_info_icon_enter(self, icon: Gtk.Widget, _event) -> bool:
        if not icon._docking_info_text:
            return False
        icon._docking_info_popover.show_all()
        icon._docking_info_popover.popup()
        return False

    def _on_info_icon_leave(self, icon: Gtk.Widget, _event) -> bool:
        icon._docking_info_popover.popdown()
        return False

    def _build_applets_tab(self) -> Gtk.Widget:
        self._applets_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=APPLET_TAB_SPACING_PX,
        )
        self._applets_box.set_border_width(APPLET_TAB_BORDER_PX)
        self._rebuild_applet_tab()
        return self._new_tab_scroller(self._applets_box)

    def _build_updates_tab(self) -> Gtk.Widget:
        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=APPEARANCE_TAB_SPACING_PX,
        )
        outer.set_border_width(APPEARANCE_TAB_BORDER_PX)

        check_now = Gtk.Button(label=_("Check Now"))
        check_now.connect("clicked", self._on_check_updates_now)
        view_releases = Gtk.Button(label=_("View Releases"))
        view_releases.connect("clicked", self._on_view_releases)

        actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=ROW_SPACING_PX,
        )
        actions.pack_start(check_now, False, False, 0)
        actions.pack_start(view_releases, False, False, 0)

        self._append_section(
            outer=outer,
            title=_("Update Checks"),
            rows=[
                (_("Check Automatically"), self._update_check_switch, None),
                (_("Frequency"), self._update_interval_combo, None),
                (_("Status"), self._update_status_label, None),
                (_("Actions"), actions, None),
            ],
        )
        return self._new_tab_scroller(outer, propagate_natural_width=True)

    def _new_tab_scroller(
        self,
        child: Gtk.Widget,
        *,
        propagate_natural_width: bool = False,
    ) -> Gtk.ScrolledWindow:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        if hasattr(scroller, "set_propagate_natural_height"):
            scroller.set_propagate_natural_height(False)
        if hasattr(scroller, "set_propagate_natural_width"):
            scroller.set_propagate_natural_width(propagate_natural_width)
        scroller.add(child)
        return scroller

    def _preferences_default_height(self) -> int:
        workarea_height = self._monitor_workarea_height()
        if workarea_height is None:
            return PREFERENCES_WINDOW_HEIGHT_PX
        available_height = max(1, workarea_height - PREFERENCES_WINDOW_SCREEN_MARGIN_PX)
        if available_height < PREFERENCES_WINDOW_MIN_HEIGHT_PX:
            return available_height
        return min(PREFERENCES_WINDOW_HEIGHT_PX, available_height)

    def _monitor_workarea_height(self) -> int | None:
        display = self._parent_display()
        if display is None:
            display = Gdk.Display.get_default()
        if display is None:
            return None

        monitor = self._parent_monitor(display)
        if monitor is None:
            monitor = display.get_primary_monitor()
        if monitor is None:
            monitor = display.get_monitor(0)
        if monitor is None:
            return None

        rect = monitor.get_workarea()
        if rect is None:
            rect = monitor.get_geometry()
        if rect is None:
            return None
        height = rect.height
        return height if isinstance(height, int) and height > 0 else None

    def _parent_display(self) -> Any:
        try:
            return self._parent.get_display()
        except Exception:
            log.debug("could not read parent display", exc_info=True)
            return None

    def _parent_monitor(self, display: Any) -> Any:
        try:
            parent_window = self._parent.get_window()
        except Exception:
            log.debug("could not read parent GDK window", exc_info=True)
            return None
        if parent_window is None:
            return None
        try:
            return display.get_monitor_at_window(parent_window)
        except Exception:
            log.debug("could not read parent monitor", exc_info=True)
            return None

    def _build_row(
        self, *, label: str, widget: Gtk.Widget, tooltip: str | None = None
    ) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=ROW_SPACING_PX)
        row.set_size_request(APPEARANCE_ROW_WIDTH_PX, -1)
        title = Gtk.Label(label=label)
        title.set_xalign(0.0)
        title.set_hexpand(True)
        row.pack_start(title, True, True, 0)
        row.pack_end(
            self._with_info_icon(widget=widget, tooltip=tooltip),
            False,
            False,
            0,
        )
        return row

    def _append_section(
        self,
        *,
        outer: Gtk.Box,
        title: str,
        rows: list[SettingsRow],
    ) -> None:
        section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=SECTION_SPACING_PX
        )
        header = self._build_section_header(title=title)
        section.pack_start(header, False, False, 0)
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=SECTION_SPACING_PX
        )
        content.set_margin_start(SECTION_CONTENT_INSET_PX)
        content.set_margin_end(SECTION_CONTENT_INSET_PX)
        for label, widget, tooltip in rows:
            content.pack_start(
                self._build_row(label=label, widget=widget, tooltip=tooltip),
                False,
                False,
                0,
            )
        section.pack_start(content, False, False, 0)
        outer.pack_start(section, False, False, 0)

    def _with_info_icon(self, *, widget: Gtk.Widget, tooltip: str | None) -> Gtk.Widget:
        if not tooltip:
            return widget
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=HIDE_MODE_BOX_SPACING_PX,
        )
        box.pack_start(widget, False, False, 0)
        box.pack_start(self._new_info_icon(tooltip), False, False, 0)
        return box

    def _build_section_header(self, *, title: str) -> Gtk.Label:
        header = Gtk.Label()
        header.set_xalign(0.0)
        header.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        header.set_margin_top(SECTION_HEADER_TOP_MARGIN_PX)
        header.set_margin_bottom(SECTION_HEADER_BOTTOM_MARGIN_PX)
        return header

    def _new_switch(self) -> Gtk.Switch:
        return Gtk.Switch()

    def _new_numeric_spin_button(
        self,
        *,
        minimum: float,
        maximum: float,
        step: float,
    ) -> Gtk.SpinButton:
        settings = Gtk.Settings.get_default()
        previous_im_module = None
        if settings is not None:
            previous_im_module = settings.get_property("gtk-im-module")
            settings.set_property("gtk-im-module", "gtk-im-context-simple")
        try:
            spin = Gtk.SpinButton.new_with_range(minimum, maximum, step)
        finally:
            if settings is not None:
                settings.set_property("gtk-im-module", previous_im_module)

        # Keep the widget on the simple context after construction too, so GTK
        # does not switch it back to the desktop-wide IM module later on.
        spin.set_property("im-module", "gtk-im-context-simple")
        return spin

    def _register_bindings(self) -> None:
        self._bindings = [
            self._register_choice_binding(
                config_attr="hide_mode",
                widget=self._hide_mode_combo,
                on_change=self._after_hide_mode_changed,
            ),
            self._register_choice_binding(
                config_attr="left_click_action",
                widget=self._left_click_combo,
            ),
            self._register_choice_binding(
                config_attr="middle_click_action",
                widget=self._middle_click_combo,
            ),
            self._register_choice_binding(
                config_attr="stack_unfold",
                widget=self._stack_unfold_combo,
            ),
            self._register_choice_binding(
                config_attr="window_list_sort",
                widget=self._window_list_sort_combo,
            ),
            self._register_switch_binding(
                config_attr="show_window_count_numbers",
                widget=self._window_count_numbers_switch,
                on_change=self._actions.queue_draw,
            ),
            self._register_switch_binding(
                config_attr="show_launcher_badges",
                widget=self._launcher_badges_switch,
                on_change=self._actions.refresh_launcher_overlay_visibility,
            ),
            self._register_switch_binding(
                config_attr="show_launcher_progress",
                widget=self._launcher_progress_switch,
                on_change=self._actions.refresh_launcher_overlay_visibility,
            ),
            self._register_switch_binding(
                config_attr="previews_enabled",
                widget=self._previews_switch,
            ),
            self._register_switch_binding(
                config_attr="tooltips_enabled",
                widget=self._tooltips_switch,
                on_change=self._after_tooltips_changed,
            ),
            self._register_switch_binding(
                config_attr="lock_icons",
                widget=self._lock_icons_switch,
                on_change=lambda: self._actions.set_icons_locked(
                    self._config.lock_icons
                ),
            ),
            self._register_switch_binding(
                config_attr="current_workspace_only",
                widget=self._workspace_only_switch,
                on_change=lambda: self._actions.set_current_workspace_only(
                    self._config.current_workspace_only
                ),
            ),
            self._register_switch_binding(
                config_attr="active_display",
                widget=self._active_display_switch,
                on_change=self._after_active_display_changed,
            ),
            self._register_switch_binding(
                config_attr="anchor_applets",
                widget=self._anchor_applets_switch,
                on_change=self._actions.queue_draw,
            ),
            self._register_switch_binding(
                config_attr="anchor_files",
                widget=self._anchor_files_switch,
                on_change=self._actions.queue_draw,
            ),
            self._register_switch_binding(
                config_attr="zoom_enabled",
                widget=self._zoom_enabled_switch,
                on_change=self._actions.queue_draw,
            ),
            self._register_switch_binding(
                config_attr="update_check_enabled",
                widget=self._update_check_switch,
            ),
            self._register_switch_binding(
                config_attr="startup_tips_enabled",
                widget=self._startup_tips_switch,
            ),
            self._register_numeric_binding(
                config_attr="update_check_interval_hours",
                widget=self._update_interval_combo,
                read_widget=self._read_update_interval_hours,
                write_widget=lambda value: self._update_interval_combo.set_active_id(
                    str(value)
                ),
                signal="changed",
            ),
            self._register_choice_binding(
                config_attr="theme",
                widget=self._theme_combo,
                on_change=self._after_theme_changed,
            ),
            self._register_choice_binding(
                config_attr="position",
                widget=self._position_combo,
                on_change=self._actions.reposition,
            ),
            self._register_int_binding(
                config_attr="icon_size",
                widget=self._icon_size_spin,
                on_change=self._after_icon_size_changed,
            ),
            self._register_numeric_binding(
                config_attr="additional_distance_from_edge",
                widget=self._additional_distance_scale,
                read_widget=lambda: int(self._additional_distance_scale.get_value()),
                write_widget=lambda value: self._additional_distance_scale.set_value(
                    cast(float, value)
                ),
                signal="value-changed",
                on_change=self._after_additional_distance_changed,
            ),
            self._register_numeric_binding(
                config_attr="transparency",
                widget=self._transparency_scale,
                read_widget=lambda: (
                    float(self._transparency_scale.get_value())
                    / TRANSPARENCY_PERCENT_SCALE
                ),
                write_widget=lambda value: self._transparency_scale.set_value(
                    cast(float, value) * TRANSPARENCY_PERCENT_SCALE
                ),
                signal="value-changed",
                on_change=self._after_transparency_changed,
            ),
            self._register_numeric_binding(
                config_attr="zoom_percent",
                widget=self._zoom_percent_spin,
                read_widget=lambda: (
                    float(self._zoom_percent_spin.get_value()) / ZOOM_PERCENT_SCALE
                ),
                write_widget=lambda value: self._zoom_percent_spin.set_value(
                    cast(float, value) * ZOOM_PERCENT_SCALE
                ),
                signal="value-changed",
                on_change=self._actions.queue_draw,
            ),
            self._register_int_binding(
                config_attr="hide_delay_ms",
                widget=self._hide_delay_spin,
            ),
            self._register_int_binding(
                config_attr="unhide_delay_ms",
                widget=self._unhide_delay_spin,
            ),
            self._register_switch_binding(
                config_attr="pressure_reveal_enabled",
                widget=self._pressure_reveal_switch,
                on_change=self._after_pressure_reveal_changed,
            ),
            self._register_int_binding(
                config_attr="pressure_threshold",
                widget=self._pressure_threshold_scale,
                on_change=self._after_pressure_reveal_changed,
            ),
            # Recent Apps
            self._register_switch_binding(
                config_attr="show_recent_apps",
                widget=self._recent_apps_switch,
                on_change=self._after_show_recent_apps_changed,
            ),
            self._register_int_binding(
                config_attr="recent_apps_max",
                widget=self._recent_apps_max_spin,
                on_change=self._after_recent_apps_policy_changed,
            ),
            self._register_int_binding(
                config_attr="recent_apps_retention_days",
                widget=self._recent_apps_retention_spin,
                on_change=self._after_recent_apps_policy_changed,
            ),
            # Recent Documents
            self._register_switch_binding(
                config_attr="show_recent_docs_in_menu",
                widget=self._recent_docs_switch,
            ),
            self._register_int_binding(
                config_attr="recent_docs_max",
                widget=self._recent_docs_max_spin,
            ),
            self._register_switch_binding(
                config_attr="global_search_enabled",
                widget=self._global_search_switch,
                on_change=self._actions.refresh_search_settings,
            ),
            self._register_numeric_binding(
                config_attr="global_search_shortcut",
                widget=self._global_search_shortcut_entry,
                read_widget=self._global_search_shortcut_entry.get_shortcut,
                write_widget=lambda value: (
                    self._global_search_shortcut_entry.set_shortcut(str(value))
                ),
                signal="shortcut-changed",
                on_change=self._actions.refresh_search_settings,
            ),
            self._register_choice_binding(
                config_attr="global_search_web_engine",
                widget=self._global_search_web_engine_combo,
                on_change=self._actions.refresh_search_settings,
            ),
        ]

    def _register_switch_binding(
        self,
        *,
        config_attr: str,
        widget: Gtk.Switch,
        on_change: Callable[[], None] | None = None,
    ) -> _ScalarBinding:
        return self._register_numeric_binding(
            config_attr=config_attr,
            widget=widget,
            read_widget=lambda: bool(widget.get_active()),
            write_widget=lambda value: widget.set_active(bool(value)),
            signal="notify::active",
            on_change=on_change,
        )

    def _register_choice_binding(
        self,
        *,
        config_attr: str,
        widget: Gtk.ComboBoxText,
        on_change: Callable[[], None] | None = None,
    ) -> _ScalarBinding:
        return self._register_numeric_binding(
            config_attr=config_attr,
            widget=widget,
            read_widget=widget.get_active_id,
            write_widget=lambda value: self._set_active_id(widget, value),
            signal="changed",
            on_change=on_change,
        )

    def _register_int_binding(
        self,
        *,
        config_attr: str,
        widget: Gtk.SpinButton,
        on_change: Callable[[], None] | None = None,
    ) -> _ScalarBinding:
        return self._register_numeric_binding(
            config_attr=config_attr,
            widget=widget,
            read_widget=lambda: int(widget.get_value()),
            write_widget=lambda value: widget.set_value(cast(float, value)),
            signal="value-changed",
            on_change=on_change,
        )

    def _register_numeric_binding(
        self,
        *,
        config_attr: str,
        widget: Gtk.Widget,
        read_widget: Callable[[], ConfigScalar | None],
        write_widget: Callable[[ConfigScalar], None],
        signal: str,
        on_change: Callable[[], None] | None = None,
    ) -> _ScalarBinding:
        binding = _ScalarBinding(
            config_attr=config_attr,
            widget=widget,
            read_widget=read_widget,
            write_widget=write_widget,
            on_change=on_change,
        )
        widget.connect(signal, lambda *_args, b=binding: self._on_binding_changed(b))
        return binding

    @staticmethod
    def _set_active_id(widget: Gtk.ComboBoxText, value: ConfigScalar) -> None:
        widget.set_active_id(str(value))

    def _sync_widgets(self) -> None:
        if self._window is None:
            return
        self._syncing_widgets = True
        try:
            for binding in self._bindings:
                binding.write_widget(getattr(self._config, binding.config_attr))
            active_ids = {
                item.desktop_id
                for item in self._model.pinned_items
                if is_applet(desktop_id=item.desktop_id)
            }
            for desktop_id, check in self._applet_checks.items():
                check.set_active(desktop_id in active_ids)
            self._sync_monitor_combo()
            self._update_updates_status()
            self._update_search_shortcut_status()
        finally:
            self._syncing_widgets = False
        self._update_dependent_sensitivity()

    def _sync_monitor_combo(self) -> None:
        if self._monitor_combo is None:
            return
        self._monitor_combo.remove_all()
        choices = self._monitor_choices()
        if not choices:
            self._monitor_combo.append("-1", _("Primary Display"))
            self._monitor_combo.set_active_id("-1")
            return
        for choice in choices:
            self._monitor_combo.append(str(choice.index), choice.label)
        self._monitor_combo.set_active_id(str(self._actions.current_monitor_choice()))

    def _update_search_shortcut_status(self) -> None:
        if self._global_search_status_label is None:
            return
        shortcut_summary = self._actions.search_shortcut_status_summary()
        shortcut_status = _("Shortcut Status: {status}").format(status=shortcut_summary)
        shortcut_description = self._actions.search_shortcut_status()
        self._global_search_status_label.set_label(shortcut_status)
        self._global_search_status_label.set_tooltip_text(shortcut_description)

    def _monitor_choices(self) -> list[Any]:
        try:
            choices = self._actions.get_monitor_choices()
        except Exception:
            return []
        if not isinstance(choices, list):
            return []
        return choices

    def _rebuild_applet_tab(self) -> None:
        box = self._applets_box
        if box is None:
            return
        for child in list(box.get_children()):
            box.remove(child)
        self._applet_checks.clear()

        try:
            catalog = get_applet_catalog()
        except Exception as exc:
            log.warning("Failed to read applet catalog for settings catalog: %s", exc)
            catalog = {}

        grouped: dict[AppletCategory, list[tuple[str, AppletMeta]]] = {
            category: [] for category in APPLET_CATEGORY_ORDER
        }
        for did, entry in sorted(catalog.items(), key=lambda item: str(item[0])):
            if did == _separator_meta.id:
                continue
            grouped[entry.category].append((did, entry))

        active_ids = {
            item.desktop_id
            for item in self._model.pinned_items
            if is_applet(desktop_id=item.desktop_id)
        }
        for category in APPLET_CATEGORY_ORDER:
            members = grouped.get(category, [])
            if not members:
                continue
            header = self._build_section_header(title=_(category.value))
            box.pack_start(header, False, False, 0)
            applet_grid = self._build_applet_grid(
                members=members,
                active_ids=active_ids,
            )
            box.pack_start(applet_grid, False, False, 0)
        return

    def _build_applet_grid(
        self,
        *,
        members: list[tuple[str, AppletMeta]],
        active_ids: set[str],
    ) -> Gtk.Widget:
        grid = Gtk.Grid()
        grid.set_column_spacing(APPLET_GRID_COLUMN_SPACING_PX)
        grid.set_row_spacing(APPLET_GRID_ROW_SPACING_PX)
        grid.set_column_homogeneous(True)
        for index, (did, entry) in enumerate(members):
            desktop_id = applet_desktop_id(applet_id=did)
            check = Gtk.CheckButton()
            check.set_active(desktop_id in active_ids)
            check.connect("toggled", self._on_applet_toggled, str(did))
            check.set_hexpand(True)
            check.set_size_request(APPLET_CELL_WIDTH_PX, -1)
            content = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=APPLET_ROW_CONTENT_SPACING_PX,
            )
            content.set_hexpand(True)
            image = self._build_applet_image(applet_id=did)
            if image is not None:
                content.pack_start(image, False, False, 0)
            title = Gtk.Label(label=entry.name)
            title.set_xalign(0.0)
            title.set_hexpand(True)
            title.set_line_wrap(True)
            content.pack_start(title, True, True, 0)
            check.add(content)
            self._applet_checks[desktop_id] = check
            grid.attach(
                check,
                index % APPLET_GRID_COLUMNS,
                index // APPLET_GRID_COLUMNS,
                1,
                1,
            )

        # Force short sections to reserve the same number of columns as fuller ones.
        for column in range(len(members), APPLET_GRID_COLUMNS):
            spacer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            spacer.set_size_request(APPLET_CELL_WIDTH_PX, -1)
            grid.attach(spacer, column, 0, 1, 1)
        return grid

    def _build_applet_image(self, *, applet_id: str) -> Gtk.Widget | None:
        pixbuf = load_catalog_icon(applet_id=applet_id, size=APPLET_LIST_ICON_PX)
        if pixbuf is None:
            return None
        image = Gtk.Image.new_from_pixbuf(pixbuf)
        image.set_pixel_size(APPLET_LIST_ICON_PX)
        return image

    def _on_destroy(self, window: Gtk.Window) -> None:
        if self._window is window:
            self._window = None

    def _on_binding_changed(self, binding: _ScalarBinding) -> None:
        if self._syncing_widgets:
            return
        value = binding.read_widget()
        if value is None:
            return
        current_value = getattr(self._config, binding.config_attr)
        if value == current_value:
            return
        setattr(self._config, binding.config_attr, value)
        self._config.save()
        if binding.on_change is not None:
            binding.on_change()
        self._update_dependent_sensitivity()

    def _read_update_interval_hours(self) -> int | None:
        active_id = self._update_interval_combo.get_active_id()
        if active_id is None:
            return None
        return int(active_id)

    def _update_updates_status(self) -> None:
        if self._update_status_label is None:
            return
        state = load_state()
        if state.last_seen_version:
            text = _("Last seen version: {version}").format(
                version=state.last_seen_version
            )
        elif state.last_checked_at:
            text = _("No update found yet")
        else:
            text = _("Not checked yet")
        self._update_status_label.set_label(text)

    def _on_check_updates_now(self, _button: Gtk.Button) -> None:
        self._actions.check_for_updates_now()

    def _on_view_releases(self, _button: Gtk.Button) -> None:
        self._actions.open_releases_page()

    def _on_monitor_combo_changed(self, widget: Gtk.ComboBoxText) -> None:
        if self._syncing_widgets:
            return
        active_id = widget.get_active_id()
        if active_id is None:
            return
        try:
            monitor_index = int(active_id)
        except ValueError:
            return
        choices = self._monitor_choices()
        connector = next(
            (
                choice.connector
                for choice in choices
                if int(getattr(choice, "index", -2)) == monitor_index
            ),
            None,
        )
        if (
            self._config.monitor_index == monitor_index
            and self._config.monitor_connector == connector
        ):
            return
        self._config.monitor_index = monitor_index
        self._config.monitor_connector = connector
        self._config.save()
        if not self._config.active_display:
            self._actions.reposition()

    def _apply_runtime_theme(self) -> None:
        theme = Theme.load(self._config.theme, self._config.icon_size).with_opacity(
            self._config.transparency
        )
        self._actions.set_theme(theme)

    def _after_theme_changed(self) -> None:
        self._apply_runtime_theme()
        self._actions.reposition()
        self._actions.queue_draw()

    def _after_icon_size_changed(self) -> None:
        self._apply_runtime_theme()
        self._actions.reposition()
        self._actions.queue_draw()

    def _after_transparency_changed(self) -> None:
        self._apply_runtime_theme()
        self._actions.queue_draw()

    def _after_additional_distance_changed(self) -> None:
        self._actions.reposition()
        self._actions.queue_draw()

    def _after_pressure_reveal_changed(self) -> None:
        self._actions.refresh_pressure_handler()
        self._update_dependent_sensitivity()

    def _after_show_recent_apps_changed(self) -> None:
        """Rebuild the recent apps section when the toggle changes."""
        self._model.rebuild_recent_apps()
        self._actions.queue_draw()
        self._update_dependent_sensitivity()

    def _after_recent_apps_policy_changed(self) -> None:
        """Redraw after a policy update that applies on the next reconciliation."""
        self._actions.queue_draw()

    def _after_hide_mode_changed(self) -> None:
        self._actions.on_hide_mode_changed()
        self._update_hide_mode_description()
        self._update_dependent_sensitivity()

    _HIDE_MODE_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "none": _("The dock is always visible and reserves screen space."),
        "always-on-top": _(
            "Always visible and floats above all windows"
            " without reserving screen space."
        ),
        "autohide": _("Hides when the mouse cursor leaves the dock."),
        "intelligent": _(
            "Hides when a window from the focused application overlaps the dock area."
        ),
        "dodge-active": _("Hides when the focused window overlaps the dock area."),
        "window-dodge": _(
            "Hides when any window on the current workspace overlaps the dock area."
        ),
        "dodge-maximized": _(
            "Hides when the focused window is maximized or a dialog overlaps the dock."
        ),
    }

    def _on_hide_mode_combo_changed(self, _widget: Gtk.ComboBoxText) -> None:
        self._update_hide_mode_description()

    def _update_hide_mode_description(self) -> None:
        if not self._hide_mode_combo or not self._hide_mode_info:
            return
        mode = self._hide_mode_combo.get_active_id() or "none"
        desc = self._HIDE_MODE_DESCRIPTIONS.get(mode, "")
        self._set_info_icon_text(self._hide_mode_info, desc)

    def _after_tooltips_changed(self) -> None:
        if not self._config.tooltips_enabled:
            self._actions.hide_tooltip()

    def _after_active_display_changed(self) -> None:
        self._actions.set_active_display(self._config.active_display)
        self._actions.reposition()

    def _update_dependent_sensitivity(self) -> None:
        if self._zoom_percent_spin is not None:
            self._zoom_percent_spin.set_sensitive(bool(self._config.zoom_enabled))
        if self._monitor_combo is not None:
            self._monitor_combo.set_sensitive(not bool(self._config.active_display))
        hide_controls_sensitive = self._config.hide_mode not in (
            "none",
            "always-on-top",
        )
        if self._hide_delay_spin is not None:
            self._hide_delay_spin.set_sensitive(hide_controls_sensitive)
        if self._unhide_delay_spin is not None:
            self._unhide_delay_spin.set_sensitive(hide_controls_sensitive)
        if self._pressure_threshold_scale is not None:
            self._pressure_threshold_scale.set_sensitive(
                bool(self._config.pressure_reveal_enabled)
            )
        recent_sensitive = bool(self._config.show_recent_apps)
        if self._recent_apps_max_spin is not None:
            self._recent_apps_max_spin.set_sensitive(recent_sensitive)
        if self._recent_apps_retention_spin is not None:
            self._recent_apps_retention_spin.set_sensitive(recent_sensitive)
        docs_sensitive = bool(self._config.show_recent_docs_in_menu)
        if self._recent_docs_max_spin is not None:
            self._recent_docs_max_spin.set_sensitive(docs_sensitive)
        search_sensitive = bool(self._config.global_search_enabled)
        if self._global_search_shortcut_entry is not None:
            self._global_search_shortcut_entry.set_sensitive(search_sensitive)
        if self._global_search_web_engine_combo is not None:
            self._global_search_web_engine_combo.set_sensitive(search_sensitive)

    def _on_applet_toggled(
        self,
        widget: Gtk.CheckButton,
        applet_id: str,
    ) -> None:
        if self._syncing_widgets:
            return
        if widget.get_active():
            self._model.add_applet(applet_id)
            return
        self._model.remove_applet(applet_desktop_id(applet_id=applet_id))
