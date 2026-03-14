"""Preferences window controller for Docking.

This module owns the dock settings window opened from the dock background menu.
The design is intentionally similar to the About controller: one controller
owns one top-level window and reuses it while it stays open.

The UI shape is inspired by Plank's preferences window:

    +----------------------------------------------+
    | Preferences                                  |
    |                                              |
    |  [ Appearance ] [ Applets ]                  |
    |                                              |
    |  appearance controls...                      |
    |  or                                          |
    |  applet enable/disable controls...           |
    +----------------------------------------------+

The dock already exposed many of these actions via the context menu. This
window does not invent a second configuration model; it gives those same
settings a persistent, easier-to-scan home.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib, Gtk

from docking.applets import get_registry
from docking.applets.base import is_applet, load_catalog_icon
from docking.applets.identity import (
    APPLET_CATEGORY_ORDER,
    AppletCategory,
    AppletId,
    applet_desktop_id,
    category_for,
)
from docking.core.config import (
    MAX_ICON_SIZE,
    MAX_ZOOM_PERCENT,
    MIN_ICON_SIZE,
    MIN_ZOOM_PERCENT,
)
from docking.core.position import Position
from docking.core.theme import _BUILTIN_THEMES_DIR, Theme
from docking.i18n import _
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.model import DockModel
    from docking.ui.runtime import DockRuntime


APPLET_LIST_ICON_PX = 32
APPLET_GRID_COLUMNS = 3
APPLET_CELL_WIDTH_PX = 168
APPEARANCE_ROW_WIDTH_PX = 428
PREFERENCES_WINDOW_WIDTH_PX = 460
PREFERENCES_WINDOW_HEIGHT_PX = 420
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
HIDE_MODE_DESC_MAX_CHARS = 28
HIDE_MODE_BOX_SPACING_PX = 4
APPLET_GRID_COLUMN_SPACING_PX = 16
APPLET_GRID_ROW_SPACING_PX = 8
APPLET_ROW_CONTENT_SPACING_PX = 6
ZOOM_PERCENT_SCALE = 100
ZOOM_PERCENT_STEP = 5
HIDE_DELAY_MAX_MS = 5000
HIDE_DELAY_STEP_MS = 50
_log = get_logger("settings")


@dataclass
class _ScalarBinding:
    """Bind one scalar config attribute to one widget."""

    config_attr: str
    widget: Gtk.Widget
    read_widget: Any
    write_widget: Any
    on_change: Any = None


class SettingsWindowController:
    """Owns the dock preferences window lifecycle and widget synchronization."""

    def __init__(
        self,
        *,
        parent: Gtk.Window,
        runtime: DockRuntime,
        model: DockModel,
        config: Config,
    ) -> None:
        self._parent = parent
        self._runtime = runtime
        self._model = model
        self._config = config
        self._window: Gtk.Window | None = None
        self._syncing_widgets = False

        self._hide_mode_combo: Any = None
        self._previews_switch: Any = None
        self._tooltips_switch: Any = None
        self._lock_icons_switch: Any = None
        self._workspace_only_switch: Any = None
        self._active_display_switch: Any = None
        self._anchor_applets_switch: Any = None
        self._anchor_files_switch: Any = None
        self._zoom_enabled_switch: Any = None
        self._theme_combo: Any = None
        self._position_combo: Any = None
        self._icon_size_spin: Any = None
        self._zoom_percent_spin: Any = None
        self._hide_delay_spin: Any = None
        self._unhide_delay_spin: Any = None
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

    def _build_window(self) -> Gtk.Window:
        window = Gtk.Window(
            title=_("Preferences"),
            transient_for=self._parent,
            destroy_with_parent=True,
        )
        window.set_default_size(
            PREFERENCES_WINDOW_WIDTH_PX,
            PREFERENCES_WINDOW_HEIGHT_PX,
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
        stack.add_titled(self._build_applets_tab(), "applets", _("Applets"))

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
        self._hide_mode_combo.set_size_request(HIDE_MODE_COMBO_WIDTH_PX, -1)
        for mode_value, mode_label in [
            ("none", _("Don't Hide")),
            ("autohide", _("Auto-hide")),
            ("intelligent", _("Intelligent")),
            ("dodge-active", _("Dodge Active Window")),
            ("window-dodge", _("Dodge All Windows")),
            ("dodge-maximized", _("Dodge Maximized")),
        ]:
            self._hide_mode_combo.append(mode_value, mode_label)

        self._hide_mode_desc = Gtk.Label()
        self._hide_mode_desc.set_xalign(0.0)
        self._hide_mode_desc.set_line_wrap(True)
        self._hide_mode_desc.set_line_wrap_mode(2)  # Pango.WrapMode.WORD_CHAR
        self._hide_mode_desc.set_max_width_chars(HIDE_MODE_DESC_MAX_CHARS)
        ctx = self._hide_mode_desc.get_style_context()
        ctx.add_class("dim-label")
        self._hide_mode_combo.connect("changed", self._on_hide_mode_combo_changed)
        self._update_hide_mode_description()

        self._previews_switch = self._new_switch()
        self._tooltips_switch = self._new_switch()
        self._lock_icons_switch = self._new_switch()
        self._workspace_only_switch = self._new_switch()
        self._active_display_switch = self._new_switch()
        self._anchor_applets_switch = self._new_switch()
        self._anchor_files_switch = self._new_switch()
        self._zoom_enabled_switch = self._new_switch()

        self._theme_combo = Gtk.ComboBoxText()
        for theme_name in sorted(p.stem for p in _BUILTIN_THEMES_DIR.glob("*.json")):
            self._theme_combo.append(theme_name, theme_name.replace("-", " ").title())

        self._position_combo = Gtk.ComboBoxText()
        for pos in Position:
            self._position_combo.append(pos.value, pos.value.capitalize())

        self._icon_size_spin = Gtk.SpinButton.new_with_range(
            MIN_ICON_SIZE,
            MAX_ICON_SIZE,
            1,
        )
        self._zoom_percent_spin = Gtk.SpinButton.new_with_range(
            int(MIN_ZOOM_PERCENT * ZOOM_PERCENT_SCALE),
            int(MAX_ZOOM_PERCENT * ZOOM_PERCENT_SCALE),
            ZOOM_PERCENT_STEP,
        )
        self._hide_delay_spin = Gtk.SpinButton.new_with_range(
            0,
            HIDE_DELAY_MAX_MS,
            HIDE_DELAY_STEP_MS,
        )
        self._unhide_delay_spin = Gtk.SpinButton.new_with_range(
            0,
            HIDE_DELAY_MAX_MS,
            HIDE_DELAY_STEP_MS,
        )

        self._register_bindings()

        self._append_section(
            outer=outer,
            title=_("Look"),
            rows=[
                (_("Theme"), self._theme_combo),
                (_("Icon Size"), self._icon_size_spin),
                (_("Zoom"), self._zoom_enabled_switch),
                (_("Zoom Percent"), self._zoom_percent_spin),
                (_("Show Tooltips"), self._tooltips_switch),
                (_("Window Previews"), self._previews_switch),
            ],
        )
        hide_mode_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=HIDE_MODE_BOX_SPACING_PX,
        )
        hide_mode_box.pack_start(self._hide_mode_combo, False, False, 0)
        hide_mode_box.pack_start(self._hide_mode_desc, False, False, 0)

        self._append_section(
            outer=outer,
            title=_("Behavior"),
            rows=[
                (_("Hide Mode"), hide_mode_box),
                (_("Hide Delay"), self._hide_delay_spin),
                (_("Unhide Delay"), self._unhide_delay_spin),
            ],
        )
        self._append_section(
            outer=outer,
            title=_("Placement"),
            rows=[
                (_("Position"), self._position_combo),
                (_("Follow Cursor"), self._active_display_switch),
                (_("Current Workspace Only"), self._workspace_only_switch),
            ],
        )
        self._append_section(
            outer=outer,
            title=_("Layout"),
            rows=[
                (_("Lock Positions"), self._lock_icons_switch),
                (_("Anchor Applets to End"), self._anchor_applets_switch),
                (_("Anchor Files to End"), self._anchor_files_switch),
            ],
        )

        return outer

    def _build_applets_tab(self) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._applets_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=APPLET_TAB_SPACING_PX,
        )
        self._applets_box.set_border_width(APPLET_TAB_BORDER_PX)
        scroller.add(self._applets_box)
        self._rebuild_applet_tab()
        return scroller

    def _build_row(self, *, label: str, widget: Gtk.Widget) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=ROW_SPACING_PX)
        row.set_size_request(APPEARANCE_ROW_WIDTH_PX, -1)
        title = Gtk.Label(label=label)
        title.set_xalign(0.0)
        title.set_hexpand(True)
        row.pack_start(title, True, True, 0)
        row.pack_end(widget, False, False, 0)
        return row

    def _append_section(
        self,
        *,
        outer: Gtk.Box,
        title: str,
        rows: list[tuple[str, Gtk.Widget]],
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
        for label, widget in rows:
            content.pack_start(
                self._build_row(label=label, widget=widget), False, False, 0
            )
        section.pack_start(content, False, False, 0)
        outer.pack_start(section, False, False, 0)

    def _build_section_header(self, *, title: str) -> Gtk.Label:
        header = Gtk.Label()
        header.set_xalign(0.0)
        header.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        header.set_margin_top(SECTION_HEADER_TOP_MARGIN_PX)
        header.set_margin_bottom(SECTION_HEADER_BOTTOM_MARGIN_PX)
        return header

    def _new_switch(self) -> Gtk.Switch:
        return Gtk.Switch()

    def _register_bindings(self) -> None:
        self._bindings = [
            self._register_choice_binding(
                config_attr="hide_mode",
                widget=self._hide_mode_combo,
                on_change=self._after_hide_mode_changed,
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
                on_change=self._runtime.set_icons_locked,
            ),
            self._register_switch_binding(
                config_attr="current_workspace_only",
                widget=self._workspace_only_switch,
                on_change=lambda _value: self._runtime.queue_draw(),
            ),
            self._register_switch_binding(
                config_attr="active_display",
                widget=self._active_display_switch,
                on_change=self._after_active_display_changed,
            ),
            self._register_switch_binding(
                config_attr="anchor_applets",
                widget=self._anchor_applets_switch,
                on_change=lambda _value: self._runtime.queue_draw(),
            ),
            self._register_switch_binding(
                config_attr="anchor_files",
                widget=self._anchor_files_switch,
                on_change=lambda _value: self._runtime.queue_draw(),
            ),
            self._register_switch_binding(
                config_attr="zoom_enabled",
                widget=self._zoom_enabled_switch,
                on_change=lambda _value: self._runtime.queue_draw(),
            ),
            self._register_choice_binding(
                config_attr="theme",
                widget=self._theme_combo,
                on_change=self._after_theme_changed,
            ),
            self._register_choice_binding(
                config_attr="position",
                widget=self._position_combo,
                on_change=lambda _value: self._runtime.reposition(),
            ),
            self._register_int_binding(
                config_attr="icon_size",
                widget=self._icon_size_spin,
                on_change=self._after_icon_size_changed,
            ),
            self._register_numeric_binding(
                config_attr="zoom_percent",
                widget=self._zoom_percent_spin,
                read_widget=lambda: (
                    float(self._zoom_percent_spin.get_value()) / ZOOM_PERCENT_SCALE
                ),
                write_widget=lambda value: self._zoom_percent_spin.set_value(
                    float(value) * ZOOM_PERCENT_SCALE
                ),
                signal="value-changed",
                on_change=lambda _value: self._runtime.queue_draw(),
            ),
            self._register_int_binding(
                config_attr="hide_delay_ms",
                widget=self._hide_delay_spin,
            ),
            self._register_int_binding(
                config_attr="unhide_delay_ms",
                widget=self._unhide_delay_spin,
            ),
        ]

    def _register_switch_binding(
        self,
        *,
        config_attr: str,
        widget: Gtk.Switch,
        on_change: Any = None,
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
        on_change: Any = None,
    ) -> _ScalarBinding:
        return self._register_numeric_binding(
            config_attr=config_attr,
            widget=widget,
            read_widget=widget.get_active_id,
            write_widget=lambda value: widget.set_active_id(str(value)),
            signal="changed",
            on_change=on_change,
        )

    def _register_int_binding(
        self,
        *,
        config_attr: str,
        widget: Gtk.SpinButton,
        on_change: Any = None,
    ) -> _ScalarBinding:
        return self._register_numeric_binding(
            config_attr=config_attr,
            widget=widget,
            read_widget=lambda: int(widget.get_value()),
            write_widget=lambda value: widget.set_value(float(value)),
            signal="value-changed",
            on_change=on_change,
        )

    def _register_numeric_binding(
        self,
        *,
        config_attr: str,
        widget: Gtk.Widget,
        read_widget: Any,
        write_widget: Any,
        signal: str,
        on_change: Any = None,
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
        finally:
            self._syncing_widgets = False
        self._update_dependent_sensitivity()

    def _rebuild_applet_tab(self) -> None:
        box = self._applets_box
        if box is None:
            return
        for child in list(box.get_children()):
            box.remove(child)
        self._applet_checks.clear()

        try:
            registry = get_registry()
        except Exception as exc:
            _log.warning("Failed to read applet registry for settings catalog: %s", exc)
            registry = {}

        grouped: dict[AppletCategory, list[tuple[AppletId, Any]]] = {
            category: [] for category in APPLET_CATEGORY_ORDER
        }
        for did, cls in sorted(registry.items(), key=lambda entry: str(entry[0])):
            try:
                did_enum = did if isinstance(did, AppletId) else AppletId(str(did))
            except ValueError:
                continue
            if did_enum == AppletId.SEPARATOR:
                continue
            grouped[category_for(applet_id=did_enum)].append((did_enum, cls))

        active_ids = {
            item.desktop_id
            for item in self._model.pinned_items
            if is_applet(desktop_id=item.desktop_id)
        }
        for category in APPLET_CATEGORY_ORDER:
            members = [
                entry
                for entry in grouped.get(category, [])
                if entry[0] != AppletId.SEPARATOR
            ]
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
        members: list[tuple[AppletId, Any]],
        active_ids: set[str],
    ) -> Gtk.Widget:
        grid = Gtk.Grid()
        grid.set_column_spacing(APPLET_GRID_COLUMN_SPACING_PX)
        grid.set_row_spacing(APPLET_GRID_ROW_SPACING_PX)
        grid.set_column_homogeneous(True)
        for index, (did, cls) in enumerate(members):
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
            title = Gtk.Label(label=cls.name)
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

    def _build_applet_image(self, *, applet_id: AppletId) -> Gtk.Widget | None:
        pixbuf = load_catalog_icon(applet_id=applet_id, size=APPLET_LIST_ICON_PX)
        if pixbuf is None:
            return None
        image = Gtk.Image.new_from_pixbuf(pixbuf)
        image.set_pixel_size(APPLET_LIST_ICON_PX)
        return image

    def _save(self) -> None:
        self._config.save()

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
        self._save()
        if binding.on_change is not None:
            binding.on_change(value)
        self._update_dependent_sensitivity()

    def _after_theme_changed(self, name: str) -> None:
        theme = Theme.load(str(name), self._config.icon_size)
        self._runtime.set_theme(theme)
        self._runtime.reposition()
        self._runtime.queue_draw()

    def _after_icon_size_changed(self, _value: int) -> None:
        self._runtime.reposition()
        self._runtime.queue_draw()

    def _after_hide_mode_changed(self, mode: str) -> None:
        if mode == "none":
            self._runtime.reset_autohide()
        self._runtime.update_struts()
        self._update_hide_mode_description()

    _HIDE_MODE_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "none": _("The dock is always visible and reserves screen space."),
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
        if not self._hide_mode_combo or not self._hide_mode_desc:
            return
        mode = self._hide_mode_combo.get_active_id() or "none"
        desc = self._HIDE_MODE_DESCRIPTIONS.get(mode, "")
        self._hide_mode_desc.set_markup(
            f"<small>{GLib.markup_escape_text(desc)}</small>"
        )

    def _after_tooltips_changed(self, active: bool) -> None:
        if not active:
            self._runtime.hide_tooltip()

    def _after_active_display_changed(self, active: bool) -> None:
        self._runtime.set_active_display(active)
        self._runtime.reposition()

    def _update_dependent_sensitivity(self) -> None:
        if self._zoom_percent_spin is not None:
            self._zoom_percent_spin.set_sensitive(bool(self._config.zoom_enabled))
        hide_controls_sensitive = self._config.hide_mode != "none"
        if self._hide_delay_spin is not None:
            self._hide_delay_spin.set_sensitive(hide_controls_sensitive)
        if self._unhide_delay_spin is not None:
            self._unhide_delay_spin.set_sensitive(hide_controls_sensitive)

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
        self._model.remove_applet(applet_desktop_id(applet_id=AppletId(applet_id)))
