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

from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

from docking.applets import get_registry
from docking.applets.base import is_applet, load_theme_icon
from docking.applets.identity import (
    APPLET_CATEGORY_ORDER,
    AppletCategory,
    AppletId,
    applet_desktop_id,
    category_for,
)
from docking.core.position import Position
from docking.core.theme import _BUILTIN_THEMES_DIR, Theme
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.model import DockModel
    from docking.ui.runtime import DockRuntime


APPLET_LIST_ICON_PX = 32
APPLET_GRID_COLUMNS = 3


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

        self._autohide_switch: Any = None
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
        window.set_default_size(560, 420)
        window.set_modal(False)
        window.set_resizable(True)
        window.set_position(Gtk.WindowPosition.CENTER)
        window.connect("destroy", self._on_destroy)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_border_width(12)

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
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_border_width(16)

        self._autohide_switch = self._new_switch(self._on_autohide_toggled)
        self._previews_switch = self._new_switch(self._on_previews_toggled)
        self._tooltips_switch = self._new_switch(self._on_tooltips_toggled)
        self._lock_icons_switch = self._new_switch(self._on_lock_icons_toggled)
        self._workspace_only_switch = self._new_switch(self._on_workspace_only_toggled)
        self._active_display_switch = self._new_switch(self._on_active_display_toggled)
        self._anchor_applets_switch = self._new_switch(self._on_anchor_applets_toggled)
        self._anchor_files_switch = self._new_switch(self._on_anchor_files_toggled)
        self._zoom_enabled_switch = self._new_switch(self._on_zoom_enabled_toggled)

        self._theme_combo = Gtk.ComboBoxText()
        for theme_name in sorted(p.stem for p in _BUILTIN_THEMES_DIR.glob("*.json")):
            self._theme_combo.append(theme_name, theme_name.replace("-", " ").title())
        self._theme_combo.connect("changed", self._on_theme_changed)

        self._position_combo = Gtk.ComboBoxText()
        for pos in Position:
            self._position_combo.append(pos.value, pos.value.capitalize())
        self._position_combo.connect("changed", self._on_position_changed)

        self._icon_size_spin = Gtk.SpinButton.new_with_range(32, 128, 1)
        self._icon_size_spin.connect("value-changed", self._on_icon_size_changed)

        self._zoom_percent_spin = Gtk.SpinButton.new_with_range(100, 400, 5)
        self._zoom_percent_spin.connect("value-changed", self._on_zoom_percent_changed)

        self._hide_delay_spin = Gtk.SpinButton.new_with_range(0, 5000, 50)
        self._hide_delay_spin.connect("value-changed", self._on_hide_delay_changed)

        self._unhide_delay_spin = Gtk.SpinButton.new_with_range(0, 5000, 50)
        self._unhide_delay_spin.connect("value-changed", self._on_unhide_delay_changed)

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
        self._append_section(
            outer=outer,
            title=_("Behavior"),
            rows=[
                (_("Auto-hide"), self._autohide_switch),
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
        self._applets_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._applets_box.set_border_width(16)
        scroller.add(self._applets_box)
        self._rebuild_applet_tab()
        return scroller

    def _build_row(self, *, label: str, widget: Gtk.Widget) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
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
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header = self._build_section_header(title=title)
        section.pack_start(header, False, False, 0)
        for label, widget in rows:
            section.pack_start(
                self._build_row(label=label, widget=widget), False, False, 0
            )
        outer.pack_start(section, False, False, 0)

    def _build_section_header(self, *, title: str) -> Gtk.Label:
        header = Gtk.Label()
        header.set_xalign(0.0)
        header.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        header.set_margin_top(6)
        header.set_margin_bottom(2)
        return header

    def _new_switch(self, callback: Any) -> Gtk.Switch:
        switch = Gtk.Switch()
        switch.connect("notify::active", callback)
        return switch

    def _sync_widgets(self) -> None:
        if self._window is None:
            return
        self._syncing_widgets = True
        try:
            self._autohide_switch.set_active(bool(self._config.autohide))
            self._previews_switch.set_active(bool(self._config.previews_enabled))
            self._tooltips_switch.set_active(bool(self._config.tooltips_enabled))
            self._lock_icons_switch.set_active(bool(self._config.lock_icons))
            self._workspace_only_switch.set_active(
                bool(self._config.current_workspace_only)
            )
            self._active_display_switch.set_active(bool(self._config.active_display))
            self._anchor_applets_switch.set_active(bool(self._config.anchor_applets))
            self._anchor_files_switch.set_active(bool(self._config.anchor_files))
            self._zoom_enabled_switch.set_active(bool(self._config.zoom_enabled))
            self._theme_combo.set_active_id(str(self._config.theme))
            self._position_combo.set_active_id(str(self._config.position))
            self._icon_size_spin.set_value(float(self._config.icon_size))
            self._zoom_percent_spin.set_value(float(self._config.zoom_percent * 100.0))
            self._hide_delay_spin.set_value(float(self._config.hide_delay_ms))
            self._unhide_delay_spin.set_value(float(self._config.unhide_delay_ms))
            active_ids = {
                item.desktop_id
                for item in self._model.pinned_items
                if is_applet(desktop_id=item.desktop_id)
            }
            for desktop_id, check in self._applet_checks.items():
                check.set_active(desktop_id in active_ids)
        finally:
            self._syncing_widgets = False

    def _rebuild_applet_tab(self) -> None:
        box = self._applets_box
        if box is None:
            return
        for child in list(box.get_children()):
            box.remove(child)
        self._applet_checks.clear()

        try:
            registry = get_registry()
        except Exception:
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
        members: list[tuple[AppletId, Any]],
        active_ids: set[str],
    ) -> Gtk.Widget:
        grid = Gtk.Grid()
        grid.set_column_spacing(16)
        grid.set_row_spacing(8)
        for index, (did, cls) in enumerate(members):
            desktop_id = applet_desktop_id(applet_id=did)
            check = Gtk.CheckButton()
            check.set_active(desktop_id in active_ids)
            check.connect("toggled", self._on_applet_toggled, str(did))
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            image = self._build_applet_image(
                desktop_id=desktop_id,
                icon_name=cls.icon_name,
            )
            if image is not None:
                content.pack_start(image, False, False, 0)
            title = Gtk.Label(label=cls.name)
            title.set_xalign(0.0)
            content.pack_start(title, False, False, 0)
            check.add(content)
            self._applet_checks[desktop_id] = check
            grid.attach(
                check,
                index % APPLET_GRID_COLUMNS,
                index // APPLET_GRID_COLUMNS,
                1,
                1,
            )
        return grid

    def _build_applet_image(
        self, *, desktop_id: str, icon_name: str
    ) -> Gtk.Widget | None:
        pixbuf = None
        applet = self._model.get_applet(desktop_id)
        if applet is not None:
            pixbuf = applet.item.icon
        if pixbuf is None and icon_name:
            pixbuf = load_theme_icon(name=str(icon_name), size=APPLET_LIST_ICON_PX)
        if pixbuf is None:
            return None
        image = Gtk.Image.new_from_pixbuf(self._small_applet_icon_pixbuf(pixbuf))
        image.set_pixel_size(APPLET_LIST_ICON_PX)
        return image

    def _small_applet_icon_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
        if (
            pixbuf.get_width() == APPLET_LIST_ICON_PX
            and pixbuf.get_height() == APPLET_LIST_ICON_PX
        ):
            return pixbuf
        scaled = pixbuf.scale_simple(
            APPLET_LIST_ICON_PX,
            APPLET_LIST_ICON_PX,
            GdkPixbuf.InterpType.BILINEAR,
        )
        return scaled or pixbuf

    def _save(self) -> None:
        self._config.save()

    def _on_destroy(self, window: Gtk.Window) -> None:
        if self._window is window:
            self._window = None

    def _on_theme_changed(self, widget: Gtk.ComboBoxText) -> None:
        if self._syncing_widgets:
            return
        name = widget.get_active_id()
        if not name or name == self._config.theme:
            return
        self._config.theme = str(name)
        self._save()
        theme = Theme.load(str(name), self._config.icon_size)
        self._runtime.set_theme(theme)
        self._runtime.reposition()
        self._runtime.queue_draw()

    def _on_position_changed(self, widget: Gtk.ComboBoxText) -> None:
        if self._syncing_widgets:
            return
        position = widget.get_active_id()
        if not position or position == self._config.position:
            return
        self._config.position = str(position)
        self._save()
        self._runtime.reposition()

    def _on_icon_size_changed(self, widget: Gtk.SpinButton) -> None:
        if self._syncing_widgets:
            return
        size = int(widget.get_value())
        if size == int(self._config.icon_size):
            return
        self._config.icon_size = size
        self._save()
        self._runtime.reposition()
        self._runtime.queue_draw()

    def _on_zoom_percent_changed(self, widget: Gtk.SpinButton) -> None:
        if self._syncing_widgets:
            return
        value = float(widget.get_value()) / 100.0
        if value == float(self._config.zoom_percent):
            return
        self._config.zoom_percent = value
        self._save()
        self._runtime.queue_draw()

    def _on_hide_delay_changed(self, widget: Gtk.SpinButton) -> None:
        if self._syncing_widgets:
            return
        value = int(widget.get_value())
        if value == int(self._config.hide_delay_ms):
            return
        self._config.hide_delay_ms = value
        self._save()

    def _on_unhide_delay_changed(self, widget: Gtk.SpinButton) -> None:
        if self._syncing_widgets:
            return
        value = int(widget.get_value())
        if value == int(self._config.unhide_delay_ms):
            return
        self._config.unhide_delay_ms = value
        self._save()

    def _on_zoom_enabled_toggled(self, widget: Gtk.Switch, _param: object) -> None:
        if self._syncing_widgets:
            return
        active = bool(widget.get_active())
        if active == bool(self._config.zoom_enabled):
            return
        self._config.zoom_enabled = active
        self._save()
        self._runtime.queue_draw()

    def _on_autohide_toggled(self, widget: Gtk.Switch, _param: object) -> None:
        if self._syncing_widgets:
            return
        active = bool(widget.get_active())
        if active == bool(self._config.autohide):
            return
        self._config.autohide = active
        self._save()
        if not active:
            self._runtime.reset_autohide()
        self._runtime.update_struts()

    def _on_previews_toggled(self, widget: Gtk.Switch, _param: object) -> None:
        if self._syncing_widgets:
            return
        active = bool(widget.get_active())
        if active == bool(self._config.previews_enabled):
            return
        self._config.previews_enabled = active
        self._save()

    def _on_tooltips_toggled(self, widget: Gtk.Switch, _param: object) -> None:
        if self._syncing_widgets:
            return
        active = bool(widget.get_active())
        if active == bool(self._config.tooltips_enabled):
            return
        self._config.tooltips_enabled = active
        self._save()
        if not active:
            self._runtime.hide_tooltip()

    def _on_lock_icons_toggled(self, widget: Gtk.Switch, _param: object) -> None:
        if self._syncing_widgets:
            return
        active = bool(widget.get_active())
        if active == bool(self._config.lock_icons):
            return
        self._config.lock_icons = active
        self._save()
        self._runtime.set_icons_locked(active)

    def _on_workspace_only_toggled(self, widget: Gtk.Switch, _param: object) -> None:
        if self._syncing_widgets:
            return
        active = bool(widget.get_active())
        if active == bool(self._config.current_workspace_only):
            return
        self._config.current_workspace_only = active
        self._save()
        self._runtime.queue_draw()

    def _on_active_display_toggled(self, widget: Gtk.Switch, _param: object) -> None:
        if self._syncing_widgets:
            return
        active = bool(widget.get_active())
        if active == bool(self._config.active_display):
            return
        self._config.active_display = active
        self._save()
        self._runtime.set_active_display(active)
        self._runtime.reposition()

    def _on_anchor_applets_toggled(self, widget: Gtk.Switch, _param: object) -> None:
        if self._syncing_widgets:
            return
        active = bool(widget.get_active())
        if active == bool(self._config.anchor_applets):
            return
        self._config.anchor_applets = active
        self._save()
        self._runtime.queue_draw()

    def _on_anchor_files_toggled(self, widget: Gtk.Switch, _param: object) -> None:
        if self._syncing_widgets:
            return
        active = bool(widget.get_active())
        if active == bool(self._config.anchor_files):
            return
        self._config.anchor_files = active
        self._save()
        self._runtime.queue_draw()

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
