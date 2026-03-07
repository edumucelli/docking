"""Right-click context menus for dock items and the dock itself."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

import docking.platform.launcher as launcher_mod
from docking.applets import get_registry
from docking.applets.base import is_applet, load_theme_icon
from docking.applets.identity import (
    APPLET_CATEGORY_ORDER,
    AppletCategory,
    AppletId,
    applet_desktop_id,
    category_for,
)
from docking.core.items import FILE_KIND, FOLDER_KIND
from docking.core.position import Position
from docking.core.theme import _BUILTIN_THEMES_DIR, Theme
from docking.i18n import _
from docking.ui.about import AboutDialogController
from docking.ui.geometry import DockGeometryFrame
from docking.ui.preview import capture_window

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel
    from docking.platform.window_tracker import WindowTracker
    from docking.ui.dock_window import DockWindow


ICON_SIZE_OPTIONS = (32, 48, 64, 80)
APPLET_MENU_ICON_PX = 16
MENU_LABEL_MAX_CHARS = 32
FOLDER_SORT_OPTIONS = (
    (_("Name"), "name"),
    (_("Kind"), "kind"),
    (_("Size"), "size"),
    (_("Created"), "created"),
    (_("Modified"), "modified"),
)
WINDOW_MENU_THUMB_W = 28
WINDOW_MENU_THUMB_H = 20
WINDOW_MENU_CLOSE_HIT_W = 44


def _make_menu_header(label: str) -> Gtk.MenuItem:
    item = Gtk.MenuItem(label=label)
    item.set_sensitive(False)
    return item


def _build_radio_submenu(
    label: str,
    items: Sequence[tuple[str, Any]],
    current: Any,
    on_changed: Any,
) -> Gtk.MenuItem:
    """Build a MenuItem with a radio-group submenu.

    Args:
        label: Submenu parent label
        items: [(display_text, value), ...] for each radio option
        current: Currently active value (compared with ==)
        on_changed: Callback(widget, value) connected to "activate"
    """
    menu_item = Gtk.MenuItem(label=label)
    submenu = Gtk.Menu()
    first: Gtk.RadioMenuItem | None = None
    for display, value in items:
        radio = Gtk.RadioMenuItem(label=display)
        if first:
            radio.join_group(first)
        else:
            first = radio
        if value == current:
            radio.set_active(True)
        radio.connect("activate", on_changed, value)
        submenu.append(radio)
    menu_item.set_submenu(submenu)
    return menu_item


def _menu_icon_pixbuf(pixbuf: GdkPixbuf.Pixbuf | None) -> GdkPixbuf.Pixbuf | None:
    """Scale pixbuf to applet submenu icon size."""
    if pixbuf is None:
        return None
    if (
        pixbuf.get_width() == APPLET_MENU_ICON_PX
        and pixbuf.get_height() == APPLET_MENU_ICON_PX
    ):
        return pixbuf
    scaled = pixbuf.scale_simple(
        APPLET_MENU_ICON_PX,
        APPLET_MENU_ICON_PX,
        GdkPixbuf.InterpType.BILINEAR,
    )
    return scaled or pixbuf


def _set_check_menu_item_icon(
    *,
    item: Gtk.CheckMenuItem,
    label: str,
    pixbuf: GdkPixbuf.Pixbuf | None,
) -> None:
    """Attach icon + text row to a check menu item."""
    item.set_label(label)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    if pixbuf is not None:
        image = Gtk.Image.new_from_pixbuf(_menu_icon_pixbuf(pixbuf))
        image.set_pixel_size(APPLET_MENU_ICON_PX)
        row.pack_start(image, False, False, 0)

    text = Gtk.Label(label=label)
    text.set_xalign(0.0)
    text.set_max_width_chars(MENU_LABEL_MAX_CHARS)
    text.set_ellipsize(Pango.EllipsizeMode.END)
    text.set_single_line_mode(True)
    row.pack_start(text, False, False, 0)

    child = item.get_child()
    if child is not None:
        item.remove(child)
    item.add(row)


def _set_menu_item_icon(
    *,
    item: Gtk.MenuItem,
    label: str,
    pixbuf: GdkPixbuf.Pixbuf | None,
    icon_px: int,
) -> None:
    item.set_label(label)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    if pixbuf is not None:
        scaled = pixbuf
        if pixbuf.get_width() != icon_px or pixbuf.get_height() != icon_px:
            scaled = pixbuf.scale_simple(
                icon_px, icon_px, GdkPixbuf.InterpType.BILINEAR
            )
        image = Gtk.Image.new_from_pixbuf(scaled or pixbuf)
        image.set_pixel_size(icon_px)
        row.pack_start(image, False, False, 0)

    text = Gtk.Label(label=label)
    text.set_xalign(0.0)
    text.set_max_width_chars(MENU_LABEL_MAX_CHARS)
    text.set_ellipsize(Pango.EllipsizeMode.END)
    text.set_single_line_mode(True)
    row.pack_start(text, False, False, 0)

    child = item.get_child()
    if child is not None:
        item.remove(child)
    item.add(row)


class MenuHandler:
    """Builds and shows context menus for dock items."""

    def __init__(
        self,
        window: DockWindow,
        model: DockModel,
        config: Config,
        tracker: WindowTracker,
        launcher: Launcher | None = None,
    ) -> None:
        self._window = window
        self._model = model
        self._config = config
        self._tracker = tracker
        self._launcher = launcher
        self._about = AboutDialogController(parent=self._window)
        self._folder_menu_monitors: dict[int, Gio.FileMonitor] = {}
        self._folder_menu_context: dict[int, tuple[Gtk.Menu, DockItem, str, bool]] = {}
        self._folder_menu_refresh_sources: dict[int, int] = {}
        self._folder_menu_signal_connected: set[int] = set()

    def show(self, event: Gdk.EventButton, cursor_main: float) -> None:
        """Build and show the right-click context menu.

        Hit-tests the cursor to determine whether to show an item-specific
        menu (desktop actions, pin/unpin, close) or a dock background menu
        (autohide, theme, position, applets, quit).
        """
        frame = self._window._get_geometry_frame(cursor_x=event.x, cursor_y=event.y)
        item = frame.item_at_point(event.x, event.y)

        if item:
            menu = self._new_popup_menu()
            self._build_item_menu(menu=menu, item=item)
        else:
            menu = self._new_popup_menu()
            insert_idx = self._insert_index(cursor_main=cursor_main, frame=frame)
            self._build_dock_menu(menu=menu, insert_index=insert_idx)

        menu.show_all()
        menu.popup_at_pointer(event)

    def show_item(self, event: Gdk.EventButton, item: DockItem) -> None:
        """Show a context menu for a known item."""
        menu = self._new_popup_menu()
        self._build_item_menu(menu=menu, item=item)
        menu.show_all()
        menu.popup_at_pointer(event)

    def _new_popup_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        self._window.on_menu_popup_opened()
        menu.connect("hide", self._on_menu_popup_closed)
        menu.connect("deactivate", self._on_menu_popup_closed)
        return menu

    def _on_menu_popup_closed(self, _menu: Gtk.Menu) -> None:
        self._cleanup_folder_menu_tree(_menu)
        self._window.on_menu_popup_closed()

    def _build_item_menu(self, menu: Gtk.Menu, item: DockItem) -> None:
        """Build context menu for a specific dock item.

        Applets: delegates to applet.get_menu_items() + "Remove from Dock".
        Regular items: desktop actions (quicklists), pin/unpin, close.
        """
        locked = self._config.lock_icons

        if is_applet(desktop_id=item.desktop_id):
            # Applet-specific menu items
            applet = self._model.get_applet(item.desktop_id)
            if applet:
                for mi in applet.get_menu_items():
                    menu.append(mi)
                if applet.get_menu_items():
                    menu.append(Gtk.SeparatorMenuItem())
            if not locked:
                remove = Gtk.MenuItem(label=_("Remove from Dock"))
                remove.connect(
                    "activate",
                    lambda _: self._model.remove_applet(item.desktop_id),
                )
                menu.append(remove)
            return

        if item.kind == FOLDER_KIND:
            self._build_folder_item_menu(menu=menu, item=item)
            return

        if item.kind == FILE_KIND:
            open_item = Gtk.MenuItem(label=_("Open"))
            open_item.connect(
                "activate", lambda _: launcher_mod.open_target(item.target)
            )
            menu.append(open_item)
            if not locked:
                menu.append(Gtk.SeparatorMenuItem())
                remove = Gtk.MenuItem(label=_("Remove from Dock"))
                remove.connect(
                    "activate", lambda _: self._model.unpin_item(item.desktop_id)
                )
                menu.append(remove)
            return

        # Desktop actions (e.g. "New Window", "New Incognito Window")
        self._append_desktop_actions(menu=menu, desktop_id=item.desktop_id)

        # Open windows — click to activate
        self._append_open_windows(menu=menu, desktop_id=item.desktop_id)

        # Pin/Unpin (hidden when icons are locked)
        if not locked:
            if item.is_pinned:
                unpin = Gtk.MenuItem(label=_("Remove from Dock"))
                unpin.connect(
                    "activate",
                    lambda _: self._model.unpin_item(item.desktop_id),
                )
                menu.append(unpin)
            else:
                pin = Gtk.MenuItem(label=_("Keep in Dock"))
                pin.connect(
                    "activate",
                    lambda _: self._model.pin_item(item.desktop_id),
                )
                menu.append(pin)

        if item.is_running and item.instance_count > 0:
            menu.append(Gtk.SeparatorMenuItem())
            label = _("Close All") if item.instance_count > 1 else _("Close")
            close = Gtk.MenuItem(label=label)
            close.connect(
                "activate",
                lambda _: self._tracker.close_all(item.desktop_id),
            )
            menu.append(close)

    def _build_folder_item_menu(self, menu: Gtk.Menu, item: DockItem) -> None:
        self._track_folder_menu(
            menu=menu, folder_item=item, target=item.target, is_root=True
        )
        self._populate_directory_menu(menu=menu, folder_item=item, target=item.target)
        menu.append(Gtk.SeparatorMenuItem())
        prefs = self._folder_prefs(item)
        menu.append(
            _build_radio_submenu(
                label=_("Sort By"),
                items=FOLDER_SORT_OPTIONS,
                current=prefs["sort"],
                on_changed=lambda widget, value, folder=item: (
                    self._on_folder_sort_changed(widget, folder, value)
                ),
            )
        )
        hidden = Gtk.CheckMenuItem(label=_("Show Hidden Files"))
        hidden.set_active(bool(prefs["show_hidden"]))
        hidden.connect("toggled", self._on_folder_hidden_toggled, item)
        menu.append(hidden)

        large = Gtk.CheckMenuItem(label=_("Large Icons"))
        large.set_active(bool(prefs["large_icons"]))
        large.connect("toggled", self._on_folder_large_icons_toggled, item)
        menu.append(large)

        if not self._config.lock_icons:
            menu.append(Gtk.SeparatorMenuItem())
            remove = Gtk.MenuItem(label=_("Remove from Dock"))
            remove.connect(
                "activate", lambda _: self._model.unpin_item(item.desktop_id)
            )
            menu.append(remove)

    def _insert_index(
        self,
        cursor_main: float,
        frame: DockGeometryFrame,
    ) -> int:
        """Compute pinned insertion index from cursor position."""
        return frame.insertion_index_for_main(cursor_main, pos=self._config.pos)

    def _build_dock_menu(self, menu: Gtk.Menu, insert_index: int = -1) -> None:
        """Build context menu for the dock background (no item under cursor).

        Sections: autohide toggle, preview toggle, theme/icon size/position
        radio submenus, applet toggle checkboxes, quit.
        """
        # Auto-hide toggle
        autohide = Gtk.CheckMenuItem(label=_("Auto-hide"))
        autohide.set_active(self._config.autohide)
        autohide.connect("toggled", self._on_autohide_toggled)
        menu.append(autohide)

        # Window previews toggle
        previews = Gtk.CheckMenuItem(label=_("Window Previews"))
        previews.set_active(self._config.previews_enabled)
        previews.connect("toggled", self._on_previews_toggled)
        menu.append(previews)

        # Monitor selection submenu (only when multiple monitors are available)
        monitor_items = self._monitor_items()
        if monitor_items:
            display_item = Gtk.MenuItem(label=_("Display"))
            display_submenu = Gtk.Menu()

            follow = Gtk.CheckMenuItem(label=_("Follow Cursor"))
            follow.set_active(self._config.active_display)
            follow.connect("toggled", self._on_active_display_toggled)
            display_submenu.append(follow)
            display_submenu.append(Gtk.SeparatorMenuItem())

            first: Gtk.RadioMenuItem | None = None
            current = self._current_monitor_choice()
            for label, value in monitor_items:
                radio = Gtk.RadioMenuItem(label=label)
                if first:
                    radio.join_group(first)
                else:
                    first = radio
                if value == current:
                    radio.set_active(True)
                radio.set_sensitive(not self._config.active_display)
                radio.connect("activate", self._on_monitor_changed, value)
                display_submenu.append(radio)

            display_item.set_submenu(display_submenu)
            menu.append(display_item)

        anchor = Gtk.CheckMenuItem(label=_("Anchor Applets to End"))
        anchor.set_active(self._config.anchor_applets)
        anchor.connect("toggled", self._on_anchor_toggled)
        menu.append(anchor)

        anchor_files = Gtk.CheckMenuItem(label=_("Anchor Files to End"))
        anchor_files.set_active(self._config.anchor_files)
        anchor_files.connect("toggled", self._on_anchor_files_toggled)
        menu.append(anchor_files)

        # Icons submenu (block/size/tooltip controls grouped together)
        icons_item = Gtk.MenuItem(label="Icons")
        icons_submenu = Gtk.Menu()

        block = Gtk.CheckMenuItem(label="Lock Positions")
        block.set_active(self._config.lock_icons)
        block.connect("toggled", self._on_lock_toggled)
        icons_submenu.append(block)

        workspace_only = Gtk.CheckMenuItem(label=_("Current Workspace Only"))
        workspace_only.set_active(self._config.current_workspace_only)
        workspace_only.connect("toggled", self._on_workspace_only_toggled)
        icons_submenu.append(workspace_only)

        size_items = [(f"{s}px", s) for s in ICON_SIZE_OPTIONS]
        icons_submenu.append(
            _build_radio_submenu(
                label="Change Size",
                items=size_items,
                current=self._config.icon_size,
                on_changed=self._on_icon_size_changed,
            )
        )

        tooltips = Gtk.CheckMenuItem(label="Show Tooltips")
        tooltips.set_active(self._config.tooltips_enabled)
        tooltips.connect("toggled", self._on_tooltips_toggled)
        icons_submenu.append(tooltips)

        icons_item.set_submenu(icons_submenu)
        menu.append(icons_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Themes submenu
        theme_names = [p.stem for p in sorted(_BUILTIN_THEMES_DIR.glob("*.json"))]
        theme_items = [(n.replace("-", " ").capitalize(), n) for n in theme_names]
        menu.append(
            _build_radio_submenu(
                label=_("Themes"),
                items=theme_items,
                current=self._config.theme,
                on_changed=self._on_theme_changed,
            )
        )

        menu.append(Gtk.SeparatorMenuItem())

        # Position submenu
        pos_items = [(p.value.capitalize(), p.value) for p in Position]
        menu.append(
            _build_radio_submenu(
                label=_("Position"),
                items=pos_items,
                current=self._config.position,
                on_changed=self._on_position_changed,
            )
        )

        # Applets submenu -- toggle each applet on/off
        try:
            registry = get_registry()
        except Exception:
            registry = {}
        if registry:
            dock_item = Gtk.MenuItem(label=_("Applets"))
            dock_menu = Gtk.Menu()
            active_ids = {
                item.desktop_id
                for item in self._model.pinned_items
                if is_applet(desktop_id=item.desktop_id)
            }
            grouped: dict[AppletCategory, list[tuple[AppletId, Any]]] = {}
            for category in APPLET_CATEGORY_ORDER:
                grouped[category] = []
            # Type narrows through category_for(AppletId).
            grouped_typed = grouped  # alias used for clarity below

            for did, cls in sorted(registry.items(), key=lambda entry: str(entry[0])):
                try:
                    did_enum = did if isinstance(did, AppletId) else AppletId(str(did))
                except ValueError:
                    continue
                if did_enum == AppletId.SEPARATOR:
                    continue
                grouped_typed[category_for(applet_id=did_enum)].append((did_enum, cls))

            non_empty_categories = [
                key for key in APPLET_CATEGORY_ORDER if grouped_typed.get(key)
            ]
            for i, category in enumerate(non_empty_categories):
                dock_menu.append(_make_menu_header(label=_(category.value)))
                for did, cls in sorted(
                    grouped_typed[category], key=lambda entry: entry[1].name.lower()
                ):
                    desktop_id = applet_desktop_id(applet_id=did)
                    check = Gtk.CheckMenuItem(label=cls.name)
                    check.set_active(desktop_id in active_ids)
                    applet = self._model.get_applet(desktop_id)
                    pixbuf: GdkPixbuf.Pixbuf | None = None
                    if applet is not None and applet.item.icon is not None:
                        pixbuf = applet.item.icon
                    else:
                        icon_name = cls.icon_name
                        if icon_name:
                            pixbuf = load_theme_icon(
                                name=str(icon_name), size=APPLET_MENU_ICON_PX
                            )
                    _set_check_menu_item_icon(item=check, label=cls.name, pixbuf=pixbuf)
                    check.connect("toggled", self._on_applet_toggled, str(did))
                    dock_menu.append(check)
                if i < len(non_empty_categories) - 1:
                    dock_menu.append(Gtk.SeparatorMenuItem())
            dock_item.set_submenu(dock_menu)
            menu.append(dock_item)

        # Add Separator (multi-instance, not a toggle)
        add_sep = Gtk.MenuItem(label=_("Add Separator"))
        add_sep.connect(
            "activate",
            lambda _, idx=insert_index: self._model.add_separator(index=idx),
        )
        menu.append(add_sep)

        menu.append(Gtk.SeparatorMenuItem())

        # About
        about_item = Gtk.MenuItem(label=_("About"))
        about_item.connect("activate", lambda _: self._about.show())
        menu.append(about_item)

        # Quit
        quit_item = Gtk.MenuItem(label=_("Quit"))
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        menu.append(quit_item)

    def _append_desktop_actions(self, menu: Gtk.Menu, desktop_id: str) -> None:
        """Append desktop actions (quicklists) from .desktop file, if any."""
        if not self._launcher:
            return

        actions = launcher_mod.get_actions(desktop_id=desktop_id)
        if not actions:
            return
        for action_id, label in actions:
            mi = Gtk.MenuItem(label=label)
            # Capture by value via default arg
            mi.connect(
                "activate",
                lambda _, did=desktop_id, aid=action_id: launcher_mod.launch_action(
                    desktop_id=did, action_id=aid
                ),
            )
            menu.append(mi)
        menu.append(Gtk.SeparatorMenuItem())

    def _append_open_windows(self, menu: Gtk.Menu, desktop_id: str) -> None:
        """Append running windows as rich menu rows with activate/close."""
        windows = self._tracker.get_windows_for(desktop_id=desktop_id)
        if not windows:
            return
        for window in windows:
            menu.append(self._build_window_menu_row(window=window))
        separator = Gtk.SeparatorMenuItem()
        setattr(separator, "_window_rows_separator", True)
        menu.append(separator)

    def _build_window_menu_row(self, window: Any) -> Gtk.MenuItem:
        xid = window.get_xid()
        title = self._tracker.get_window_title_for_xid(xid) or _("Window")
        row = Gtk.MenuItem()
        row.set_label(title)
        thumb = capture_window(
            wnck_window=window, thumb_w=WINDOW_MENU_THUMB_W, thumb_h=WINDOW_MENU_THUMB_H
        )

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        image = Gtk.Image.new_from_pixbuf(thumb) if thumb is not None else Gtk.Image()
        image.set_pixel_size(WINDOW_MENU_THUMB_H)
        box.pack_start(image, False, False, 0)

        text = Gtk.Label(label=title)
        text.set_xalign(0.0)
        text.set_max_width_chars(MENU_LABEL_MAX_CHARS)
        text.set_ellipsize(Pango.EllipsizeMode.END)
        text.set_single_line_mode(True)
        text.set_hexpand(True)
        box.pack_start(text, True, True, 0)

        close_label = Gtk.Label(label="\u00d7")
        close_label.set_xalign(0.5)
        close_label.set_margin_end(12)
        box.pack_end(close_label, False, False, 0)

        child = row.get_child()
        if child is not None:
            row.remove(child)
        row.add(box)
        setattr(row, "_window_row", True)
        row.connect("button-press-event", self._on_window_row_button_press, xid)
        row.connect("button-release-event", self._on_window_row_button_release, xid)
        row.connect("activate", lambda *_a: self._tracker.activate_xid(xid))
        return row

    def _window_close_zone_hit(self, widget: Gtk.Widget, event: Any) -> bool:
        x = float(getattr(event, "x", -1.0))
        if x < 0:
            return False
        get_allocation = getattr(widget, "get_allocation", None)
        if not callable(get_allocation):
            return False
        alloc = get_allocation()
        width = float(getattr(alloc, "width", 0.0))
        return width > 0 and x >= max(0.0, width - WINDOW_MENU_CLOSE_HIT_W)

    def _on_window_row_button_press(
        self, widget: Gtk.Widget, event: Any, xid: int
    ) -> bool:
        return self._window_close_zone_hit(widget=widget, event=event)

    def _on_window_row_button_release(
        self, widget: Gtk.Widget, event: Any, xid: int
    ) -> bool:
        if not self._window_close_zone_hit(widget=widget, event=event):
            return False
        self._tracker.close_xid(xid)
        self._remove_window_row(widget=widget, event=event)
        self._hide_window_hover_ui()
        return True

    def _remove_window_row(self, widget: Gtk.Widget, event: Any | None = None) -> None:
        parent = getattr(widget, "get_parent", lambda: None)()
        if parent is None or not hasattr(parent, "remove"):
            return
        hide = getattr(widget, "hide", None)
        if callable(hide):
            hide()
        parent.remove(widget)
        destroy = getattr(widget, "destroy", None)
        if callable(destroy):
            destroy()
        children = list(getattr(parent, "get_children", lambda: [])())
        if not any(getattr(child, "_window_row", False) for child in children):
            for child in children:
                if getattr(child, "_window_rows_separator", False):
                    child_hide = getattr(child, "hide", None)
                    if callable(child_hide):
                        child_hide()
                    parent.remove(child)
                    destroy = getattr(child, "destroy", None)
                    if callable(destroy):
                        destroy()
                    break
        self._refresh_live_menu(parent=parent, event=event)

    def _refresh_live_menu(self, parent: Gtk.Widget, event: Any | None = None) -> None:
        popdown = getattr(parent, "popdown", None)
        if callable(popdown):
            popdown()
        show_all = getattr(parent, "show_all", None)
        if callable(show_all):
            show_all()
        queue_resize = getattr(parent, "queue_resize", None)
        if callable(queue_resize):
            queue_resize()
        check_resize = getattr(parent, "check_resize", None)
        if callable(check_resize):
            check_resize()
        queue_draw = getattr(parent, "queue_draw", None)
        if callable(queue_draw):
            queue_draw()
        popup_at_pointer = getattr(parent, "popup_at_pointer", None)
        if callable(popup_at_pointer):
            popup_at_pointer(event)

    def _hide_window_hover_ui(self) -> None:
        tooltip = getattr(self._window, "_tooltip", None)
        if tooltip is not None:
            tooltip.hide()
        preview = getattr(self._window, "_preview", None)
        if preview is not None:
            preview.hide()

    def _on_applet_toggled(self, widget: Gtk.CheckMenuItem, applet_id: str) -> None:
        if widget.get_active():
            self._model.add_applet(applet_id)
        else:
            self._model.remove_applet(applet_desktop_id(applet_id=AppletId(applet_id)))

    def _on_autohide_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._config.autohide = widget.get_active()
        self._config.save()
        # Reset hide state when toggling off so dock becomes visible immediately
        if not self._config.autohide and self._window.autohide:
            self._window.autohide.reset()
        # Update struts immediately so windows adapt to the new mode:
        # autohide on  -> clear struts (windows use full screen)
        # autohide off -> set struts (windows shrink above dock)
        self._window.update_struts()

    def _on_previews_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._config.previews_enabled = widget.get_active()
        self._config.save()

    def _monitor_items(self) -> list[tuple[str, int]]:
        getter = getattr(self._window, "get_monitor_menu_choices", None)
        if not callable(getter):
            return []
        try:
            raw = getter()
        except Exception:
            return []
        if not isinstance(raw, list):
            return []

        items: list[tuple[str, int]] = []
        for entry in raw:
            if (
                isinstance(entry, tuple)
                and len(entry) == 2
                and isinstance(entry[0], str)
                and isinstance(entry[1], int)
            ):
                items.append((entry[0], entry[1]))
        return items

    def _current_monitor_choice(self) -> int:
        getter = getattr(self._window, "current_monitor_choice", None)
        if callable(getter):
            try:
                value = getter()
                if isinstance(value, int):
                    return value
            except Exception:
                pass
        return int(self._config.monitor_index)

    def _on_monitor_changed(self, widget: Gtk.MenuItem, monitor_index: int) -> None:
        if not widget.get_active():
            return
        primary_idx = monitor_index
        getter = getattr(self._window, "primary_monitor_index", None)
        if callable(getter):
            try:
                value = getter()
                if isinstance(value, int):
                    primary_idx = value
            except Exception:
                pass
        new_value = -1 if monitor_index == primary_idx else monitor_index
        if int(self._config.monitor_index) == new_value:
            return
        self._config.monitor_index = new_value
        self._config.save()
        self._window.reposition()

    def _on_active_display_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._config.active_display = widget.get_active()
        self._config.save()
        if self._config.active_display:
            self._window.start_active_display()
        else:
            self._window.stop_active_display()
        self._window.reposition()

    def _on_lock_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._config.lock_icons = widget.get_active()
        self._config.save()
        if self._window._dnd:
            self._window._dnd.set_locked(self._config.lock_icons)

    def _on_anchor_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._config.anchor_applets = widget.get_active()
        self._config.save()
        self._window.queue_draw()

    def _on_anchor_files_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._config.anchor_files = widget.get_active()
        self._config.save()
        self._window.queue_draw()

    def _on_workspace_only_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._config.current_workspace_only = widget.get_active()
        self._config.save()
        self._window.queue_draw()

    def _on_tooltips_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._config.tooltips_enabled = widget.get_active()
        self._config.save()
        if not self._config.tooltips_enabled:
            self._window._tooltip.hide()

    def _on_theme_changed(self, widget: Gtk.MenuItem, name: str) -> None:
        if not widget.get_active() or name == self._config.theme:
            return
        self._config.theme = name
        self._config.save()
        new_theme = Theme.load(name, self._config.icon_size)
        self._window.theme = new_theme
        self._window.reposition()
        self._window.drawing_area.queue_draw()

    def _on_position_changed(self, widget: Gtk.MenuItem, position: str) -> None:
        if not widget.get_active() or position == self._config.position:
            return
        self._config.position = position
        self._config.save()
        self._window.reposition()

    def _on_icon_size_changed(self, widget: Gtk.MenuItem, size: int) -> None:
        if widget.get_active():
            self._config.icon_size = size
            self._config.save()
            # Would need a full reload to update icons at new size

    def _folder_prefs(self, item: DockItem) -> dict[str, Any]:
        item_prefs = self._config.item_prefs
        stored = dict(item_prefs.get(item.prefs_key or item.target, {}))
        return {
            "sort": stored.get("sort", "name"),
            "show_hidden": bool(stored.get("show_hidden", False)),
            "large_icons": bool(stored.get("large_icons", False)),
        }

    def _save_folder_prefs(self, item: DockItem, prefs: dict[str, Any]) -> None:
        self._config.item_prefs[item.prefs_key or item.target] = prefs
        self._config.save()
        self._window.queue_draw()

    def _populate_directory_menu(
        self, menu: Gtk.Menu, folder_item: DockItem, target: str
    ) -> None:
        rows = self._list_directory(folder_item=folder_item, target=target)
        self._append_directory_rows(menu=menu, folder_item=folder_item, rows=rows)

    def _append_directory_rows(
        self,
        menu: Gtk.Menu,
        folder_item: DockItem,
        rows: list[dict[str, Any]],
    ) -> None:
        for child in rows:
            self._append_directory_row(menu=menu, folder_item=folder_item, child=child)

    def _append_directory_row(
        self, menu: Gtk.Menu, folder_item: DockItem, child: dict[str, Any]
    ) -> None:
        row = Gtk.MenuItem(label=child["name"])
        _set_menu_item_icon(
            item=row,
            label=child["name"],
            pixbuf=child["icon"],
            icon_px=self._folder_icon_px(folder_item=folder_item),
        )
        if child["is_dir"]:
            if not child.get("has_children", False):
                row.connect(
                    "activate",
                    lambda _, child_target=child["target"]: launcher_mod.open_target(
                        child_target
                    ),
                )
                menu.append(row)
                return
            submenu = Gtk.Menu()
            submenu.connect(
                "show",
                self._on_folder_submenu_show,
                folder_item,
                child["target"],
            )
            row.set_submenu(submenu)
        else:
            row.connect(
                "activate",
                lambda _, child_target=child["target"]: launcher_mod.open_target(
                    child_target
                ),
            )
        menu.append(row)

    def _on_folder_submenu_show(
        self, menu: Gtk.Menu, folder_item: DockItem, target: str
    ) -> None:
        self._track_folder_menu(
            menu=menu, folder_item=folder_item, target=target, is_root=False
        )
        if menu.get_children():
            return
        self._populate_directory_menu(menu=menu, folder_item=folder_item, target=target)
        menu.show_all()

    def _track_folder_menu(
        self, menu: Gtk.Menu, folder_item: DockItem, target: str, is_root: bool
    ) -> None:
        menu_id = id(menu)
        self._folder_menu_context[menu_id] = (menu, folder_item, target, is_root)
        if menu_id not in self._folder_menu_signal_connected:
            menu.connect("hide", self._on_folder_menu_hidden)
            self._folder_menu_signal_connected.add(menu_id)

        if menu_id in self._folder_menu_monitors:
            return

        uri = launcher_mod.normalize_file_target(target)
        if uri is None:
            return
        try:
            folder = Gio.File.new_for_uri(uri)
            monitor = folder.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            monitor.connect("changed", self._on_folder_menu_changed, menu_id)
            self._folder_menu_monitors[menu_id] = monitor
        except GLib.Error:
            return

    def _on_folder_menu_hidden(self, menu: Gtk.Menu) -> None:
        self._cleanup_folder_menu_tree(menu)

    def _on_folder_menu_changed(
        self,
        _monitor: Gio.FileMonitor,
        _file: Gio.File,
        _other_file: Gio.File | None,
        _event_type: Gio.FileMonitorEvent,
        menu_id: int,
    ) -> None:
        existing = self._folder_menu_refresh_sources.pop(menu_id, 0)
        if existing:
            GLib.source_remove(existing)
        source = GLib.timeout_add(120, self._refresh_folder_menu, menu_id)
        self._folder_menu_refresh_sources[menu_id] = source

    def _refresh_folder_menu(self, menu_id: int) -> bool:
        self._folder_menu_refresh_sources.pop(menu_id, None)
        context = self._folder_menu_context.get(menu_id)
        if context is None:
            return False
        menu, folder_item, target, is_root = context
        self._clear_menu_children(menu)
        if is_root:
            self._build_folder_item_menu(menu=menu, item=folder_item)
        else:
            self._populate_directory_menu(
                menu=menu, folder_item=folder_item, target=target
            )
        menu.show_all()
        return False

    def _clear_menu_children(self, menu: Gtk.Menu) -> None:
        for child in list(menu.get_children()):
            submenu = getattr(child, "get_submenu", lambda: None)()
            if submenu is not None:
                self._cleanup_folder_menu_tree(submenu)
            menu.remove(child)

    def _cleanup_folder_menu_tree(self, menu: Gtk.Menu) -> None:
        for child in list(menu.get_children()):
            submenu = getattr(child, "get_submenu", lambda: None)()
            if submenu is not None:
                self._cleanup_folder_menu_tree(submenu)
        self._cleanup_folder_menu(menu)

    def _cleanup_folder_menu(self, menu: Gtk.Menu) -> None:
        menu_id = id(menu)
        refresh_source = self._folder_menu_refresh_sources.pop(menu_id, 0)
        if refresh_source:
            GLib.source_remove(refresh_source)
        monitor = self._folder_menu_monitors.pop(menu_id, None)
        if monitor is not None:
            monitor.cancel()
        self._folder_menu_context.pop(menu_id, None)
        self._folder_menu_signal_connected.discard(menu_id)

    def _list_directory(
        self, folder_item: DockItem, target: str
    ) -> list[dict[str, Any]]:
        uri = launcher_mod.normalize_file_target(target)
        if uri is None:
            return []
        try:
            folder = Gio.File.new_for_uri(uri)
            enumerator = folder.enumerate_children(
                ",".join(
                    (
                        "standard::name",
                        "standard::display-name",
                        "standard::icon",
                        "standard::type",
                        "standard::is-hidden",
                        "standard::size",
                        "time::created",
                        "time::modified",
                    )
                ),
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except Exception:
            return []

        prefs = self._folder_prefs(folder_item)
        rows: list[dict[str, Any]] = []
        while True:
            info = enumerator.next_file(None)
            if info is None:
                break
            if info.get_is_hidden() and not prefs["show_hidden"]:
                continue
            child = folder.get_child(info.get_name())
            child_uri = child.get_uri()
            icon = info.get_icon()
            is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
            rows.append(
                {
                    "target": child_uri,
                    "name": info.get_display_name() or info.get_name(),
                    "kind": "dir" if is_dir else "file",
                    "is_dir": is_dir,
                    "has_children": (
                        self._directory_has_visible_children(
                            target=child_uri,
                            show_hidden=bool(prefs["show_hidden"]),
                        )
                        if is_dir
                        else False
                    ),
                    "size": int(info.get_size()),
                    "created": int(info.get_attribute_uint64("time::created")),
                    "modified": int(info.get_attribute_uint64("time::modified")),
                    "icon": (
                        self._launcher.load_gicon(
                            gicon=icon,
                            size=self._folder_icon_px(folder_item=folder_item),
                        )
                        or self._launcher.load_icon(
                            icon_name=(
                                "folder"
                                if info.get_file_type() == Gio.FileType.DIRECTORY
                                else "text-x-generic"
                            ),
                            size=self._folder_icon_px(folder_item=folder_item),
                        )
                    )
                    if self._launcher
                    else None,
                }
            )
        rows.sort(key=lambda row: self._folder_sort_key(row=row, mode=prefs["sort"]))
        return rows

    def _directory_has_visible_children(self, target: str, show_hidden: bool) -> bool:
        uri = launcher_mod.normalize_file_target(target)
        if uri is None:
            return False
        try:
            folder = Gio.File.new_for_uri(uri)
            enumerator = folder.enumerate_children(
                "standard::is-hidden",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except Exception:
            return False

        while True:
            info = enumerator.next_file(None)
            if info is None:
                return False
            if show_hidden or not info.get_is_hidden():
                return True

    def _folder_sort_key(self, row: dict[str, Any], mode: str) -> tuple[Any, ...]:
        if mode == "kind":
            return (row["kind"], row["name"].casefold())
        if mode == "size":
            return (row["size"], row["name"].casefold())
        if mode == "created":
            return (row["created"], row["name"].casefold())
        if mode == "modified":
            return (row["modified"], row["name"].casefold())
        return (row["name"].casefold(),)

    def _folder_icon_px(self, folder_item: DockItem) -> int:
        prefs = self._folder_prefs(folder_item)
        if prefs["large_icons"]:
            return 24
        return 16

    def _update_folder_pref(self, item: DockItem, key: str, value: Any) -> None:
        prefs = self._folder_prefs(item)
        prefs[key] = value
        self._save_folder_prefs(item, prefs)
        self._model.refresh_folder_preview(desktop_id=item.desktop_id, notify=False)

    def _on_folder_sort_changed(
        self, widget: Gtk.MenuItem, item: DockItem, value: str
    ) -> None:
        if widget.get_active():
            self._update_folder_pref(item, "sort", value)

    def _on_folder_hidden_toggled(
        self, widget: Gtk.CheckMenuItem, item: DockItem
    ) -> None:
        self._update_folder_pref(item, "show_hidden", widget.get_active())

    def _on_folder_large_icons_toggled(
        self, widget: Gtk.CheckMenuItem, item: DockItem
    ) -> None:
        self._update_folder_pref(item, "large_icons", widget.get_active())

    def _hit_test(
        self,
        main_coord: float,
        items: list[DockItem],
        frame: DockGeometryFrame,
    ) -> DockItem | None:
        """Find which DockItem is under cursor along the main axis."""
        if self._window.cursor_x < 0 or self._window.cursor_y < 0:
            return None
        index = frame.item_index_at_point(self._window.cursor_x, self._window.cursor_y)
        if index < 0 or index >= len(items):
            return None
        return items[index]
