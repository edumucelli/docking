"""Context menu construction for dock items, applets, folders, and background.

Why the dock menu logic is centralized

Right-click behavior in a dock is deceptively broad. Depending on where the
pointer is, the same action can mean:

- item menu for an application launcher,
- applet menu with applet-specific actions,
- folder stack menu,
- dock background menu,
- live menu for currently open application windows.

If each item type built its own menus independently, the dock would lose
consistency in:

- popup lifecycle,
- autohide/menu interaction,
- item targeting,
- icon/title formatting,
- shared commands like pin/unpin, lock positions, theme changes, and position.

This module is the centralized menu builder for those cases.

What this module owns

MenuHandler owns:

- deciding whether a right-click targets an item or the dock background,
- constructing GTK menu trees,
- building item-specific and dock-specific actions,
- applet submenu organization,
- folder stack menus,
- live window menu entries with thumbnails,
- popup creation and lifecycle hookup.

It does not own:

- dock geometry,
- autohide policy directly,
- tooltip/preview lifecycle directly,
- actual runtime side effects on the dock shell.

Those imperative side effects are routed through `DockRuntime`.

Why geometry matters for menus

The dock must support this state:

    pointer inside dock
      but
    pointer not on any item

That is what makes the dock background menu reachable.

So menu targeting follows shared geometry:

    event point
      |
      +--> item_at_point(...) ? ---- yes --> build item/applet/folder menu
      |
      +--> no -----------------------> build dock background menu

This is one of the reasons click/hit geometry is intentionally narrower than
hover geometry. The background needs to remain a real target.

Runtime command boundary

This module is intentionally not allowed to mutate DockWindow internals freely.
The important split is:

- MenuHandler
  decides what commands exist and when they are offered

- DockRuntime
  performs dock-wide side effects such as:
    - menu popup open/close hooks,
    - reposition,
    - strut updates,
    - redraws,
    - active-display toggles,
    - hover UI cleanup

That boundary matters because menu code is broad enough already; it should not
also become the place where raw window internals are orchestrated directly.

Kinds of menus built here

1. Application item menu
   Launch, pin/unpin, close windows, desktop actions, etc.

2. Applet menu
   Applet-specific commands and applet insertion choices.

3. Folder stack menu
   A live view into a directory with sorting/filtering behavior.

4. Dock background menu
   Global dock behavior such as:
   - position
   - autohide
   - icon options
   - theme selection
   - applet insertion
   - quit/about

Window thumbnails in menus

For running applications, the menu may include live window entries. Those use
the same preview capture machinery as the preview popup, but at smaller sizes.
That gives the user recognition value directly inside the menu without having to
switch to the larger preview surface.

Popup lifecycle

Menu popups affect dock behavior even though they are not part of the dock
window itself:

    menu opens
      |
      +--> runtime.menu_popup_opened()
      |
      +--> autohide disabled while menu is active

    menu hides
      |
      +--> runtime.menu_popup_closed()
      |
      +--> interaction policy re-evaluates pointer position

That lifecycle is why menu creation and menu popup hookup are not separate
concerns in practice.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo

import docking.platform.launcher as launcher_mod
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
from docking.core.items import FILE_KIND, FOLDER_KIND
from docking.i18n import _
from docking.log import get_logger
from docking.ui.about import AboutDialogController
from docking.ui.display import clamp_to_screen
from docking.ui.geometry import DockGeometryBuilder, DockGeometryFrame
from docking.ui.preview import capture_window
from docking.ui.runtime import DockRuntime
from docking.ui.settings import SettingsWindowController
from docking.ui.shelf import rounded_rect

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel
    from docking.platform.window_tracker import WindowTracker


APPLET_MENU_ICON_PX = 16
MENU_LABEL_MAX_CHARS = 32
MENU_ROW_SPACING_PX = 6
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
WINDOW_MENU_CLOSE_LABEL_XALIGN = 0.5
WINDOW_MENU_CLOSE_MARGIN_END_PX = 12
FOLDER_MENU_REFRESH_DEBOUNCE_MS = 120
FOLDER_SMALL_ICON_PX = 16
FOLDER_LARGE_ICON_PX = 24
FOLDER_STACK_MAX_VISIBLE_ROWS = 9
FOLDER_STACK_GAP_PX = 8
FOLDER_STACK_POPUP_SIDE_PADDING_PX = 14
FOLDER_STACK_TOP_PADDING_PX = 6
FOLDER_STACK_ACTION_GAP_PX = 18
FOLDER_STACK_ICON_GAP_PX = 10
FOLDER_STACK_LABEL_HEIGHT_PX = 24
FOLDER_STACK_LABEL_MAX_WIDTH_PX = 148
FOLDER_STACK_ACTION_MAX_WIDTH_PX = 240
FOLDER_STACK_ROW_STEP_PX = 54
FOLDER_STACK_CURVE_X_PX = 40
FOLDER_STACK_ARC_BASE_SHIFT_PX = 8
FOLDER_STACK_ARC_RADIUS_FACTOR = 2.45
FOLDER_STACK_ARC_LINEAR_BLEND = 0.34
FOLDER_STACK_RIGHT_BLEED_PX = 24
FOLDER_STACK_LABEL_RADIUS_PX = 6
FOLDER_STACK_LABEL_TEXT_MARGIN_PX = 8
FOLDER_STACK_ACTION_ARROW_GAP_PX = 7
FOLDER_STACK_ACTION_ARROW_SIZE_PX = 7
FOLDER_STACK_ROTATION_MAX_DEG = 5.5
FOLDER_STACK_REVEAL_DURATION_MS = 160
FOLDER_STACK_REVEAL_STAGGER_MS = 28
FOLDER_STACK_ANIM_FRAME_MS = 16
FOLDER_STACK_HOVER_SCALE = 1.14
FOLDER_STACK_HOVER_EASE = 0.35
log = get_logger("menu")

SUPPORT_URL = "https://github.com/edumucelli/docking/issues"


@dataclass(frozen=True)
class FolderStackCard:
    label: str
    target: str | None
    icon: GdkPixbuf.Pixbuf | None
    icon_x: int
    icon_y: int
    icon_size: int
    label_x: int
    label_y: int
    label_w: int
    label_h: int
    centered: bool = False
    stack_progress: float = 0.0
    arc_span: float = 0.0


@dataclass(frozen=True)
class FolderStackCardGeometry:
    reveal: float
    hover_value: float
    rotation_radians: float
    icon_x: float
    icon_y: float
    icon_size: float
    icon_center_x: float
    icon_center_y: float
    label_x: float
    label_y: float


def _is_folder_stack_action_card(card: FolderStackCard) -> bool:
    return card.centered and card.target is not None and card.icon is None


def _ease_out_cubic(value: float) -> float:
    value = min(max(value, 0.0), 1.0)
    return 1.0 - (1.0 - value) ** 3


def _folder_stack_arc_offset(progress: float, span: float) -> float:
    progress = min(max(progress, 0.0), 1.0)
    max_offset = max(FOLDER_STACK_CURVE_X_PX, span * 0.08)
    linear = max_offset * progress
    if span <= 0:
        curved = linear
    else:
        radius = max(span * FOLDER_STACK_ARC_RADIUS_FACTOR, span + 1.0)
        y = progress * span
        curved = radius - math.sqrt(max(radius * radius - y * y, 0.0))
        max_curved = radius - math.sqrt(max(radius * radius - span * span, 0.0))
        curved = max_offset * (curved / max_curved) if max_curved > 0 else linear
    offset = (
        FOLDER_STACK_ARC_LINEAR_BLEND * linear
        + (1.0 - FOLDER_STACK_ARC_LINEAR_BLEND) * curved
    )
    return FOLDER_STACK_ARC_BASE_SHIFT_PX + offset


def _folder_stack_rotation(progress: float, position: Any, span: float) -> float:
    progress = min(max(progress, 0.0), 1.0)
    direction = 1.0 if position in {"bottom", "left"} else -1.0
    degrees = min(
        (0.2 + 0.8 * progress) * FOLDER_STACK_ROTATION_MAX_DEG,
        FOLDER_STACK_ROTATION_MAX_DEG,
    )
    return math.radians(degrees * direction)


def _measure_stack_text_px(text: str) -> int:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    cr = cairo.Context(surface)
    layout = PangoCairo.create_layout(cr)
    layout.set_text(text, -1)
    desc = Pango.FontDescription()
    desc.set_family("Sans")
    desc.set_size(10 * Pango.SCALE)
    layout.set_font_description(desc)
    _ink, logical = layout.get_pixel_extents()
    return max(int(logical.width), 0)


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


def _set_menu_item_icon(
    *,
    item: Gtk.MenuItem,
    label: str,
    pixbuf: GdkPixbuf.Pixbuf | None,
    icon_px: int,
) -> None:
    item.set_label(label)
    row = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=MENU_ROW_SPACING_PX,
    )
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
        about: AboutDialogController,
        settings: SettingsWindowController,
        runtime: DockRuntime,
        model: DockModel,
        config: Config,
        window_tracker: WindowTracker,
        geometry_builder: DockGeometryBuilder,
        launcher: Launcher | None = None,
    ) -> None:
        self._about = about
        self._settings = settings
        self._runtime = runtime
        self._model = model
        self._config = config
        self._tracker = window_tracker
        self._launcher = launcher
        self._geometry_builder = geometry_builder
        self._folder_menu_monitors: dict[int, Gio.FileMonitor] = {}
        self._folder_menu_context: dict[int, tuple[Gtk.Menu, DockItem, str, bool]] = {}
        self._folder_menu_refresh_sources: dict[int, int] = {}
        self._folder_menu_signal_connected: set[int] = set()
        self._folder_stack_window: Gtk.Window | None = None
        self._folder_stack_revealer: Gtk.Revealer | None = None
        self._folder_stack_item: DockItem | None = None
        self._folder_stack_anchor_x: int = 0
        self._folder_stack_anchor_y: int = 0
        self._folder_stack_icon_w: int = 0
        self._folder_stack_fold_center_x: int = 0
        self._folder_stack_position_value = self._config.pos
        self._folder_stack_area: Gtk.DrawingArea | None = None
        self._folder_stack_cards: list[FolderStackCard] = []
        self._folder_stack_monitor: Gio.FileMonitor | None = None
        self._folder_stack_refresh_source: int = 0
        self._folder_stack_anim_source: int = 0
        self._folder_stack_show_started_us: int = 0
        self._folder_stack_hover_target: str | None = None
        self._folder_stack_hover_values: dict[str, float] = {}
        self._folder_stack_pressed_target: str | None = None

    def show(self, event: Gdk.EventButton, cursor_main: float) -> None:
        """Build and show the right-click context menu.

        Hit-tests the cursor to determine whether to show an item-specific
        menu (desktop actions, pin/unpin, close) or a dock background menu
        (autohide, theme, position, applets, quit).
        """
        frame = self._geometry_builder.build_frame(cursor_x=event.x, cursor_y=event.y)
        item = frame.item_at_point(event.x, event.y)
        self._close_folder_stack()

        if item:
            menu = self._new_popup_menu()
            self._build_item_menu(menu=menu, item=item)
        else:
            menu = self._new_popup_menu()
            insert_idx = self._insert_index(cursor_main=cursor_main, frame=frame)
            self._build_dock_menu(menu=menu, insert_index=insert_idx)

        menu.show_all()
        menu.popup_at_pointer(event)

    def show_folder_stack(
        self,
        *,
        item: DockItem,
        anchor_x: int,
        anchor_y: int,
        icon_w: int,
        position: Any,
    ) -> None:
        """Show or toggle the left-click folder stack popup for a pinned folder."""
        if (
            self._folder_stack_window is not None
            and self._folder_stack_window.get_visible()
            and self._folder_stack_item is not None
            and self._folder_stack_item.desktop_id == item.desktop_id
        ):
            self._close_folder_stack()
            return

        self._close_folder_stack()
        self._runtime.hide_hover_ui()
        self._runtime.menu_popup_opened()

        window = self._ensure_folder_stack_window()
        revealer = self._folder_stack_revealer
        assert revealer is not None

        self._replace_folder_stack_content(item=item)

        self._folder_stack_anchor_x = int(anchor_x)
        self._folder_stack_anchor_y = int(anchor_y)
        self._folder_stack_icon_w = max(int(icon_w), 1)
        self._folder_stack_position_value = position
        self._folder_stack_item = item
        self._track_folder_stack(target=item.target)
        self._restart_folder_stack_animation()
        self._position_folder_stack_window()
        revealer.set_reveal_child(True)
        window.show_all()

    def close_folder_stack(self) -> None:
        """Close the left-click folder stack if it is currently visible."""
        self._close_folder_stack()

    def open_folder_stack_item_id(self) -> str | None:
        """Return the folder item id that currently owns the visible stack."""
        window = self._folder_stack_window
        if (
            window is None
            or not window.get_visible()
            or self._folder_stack_item is None
        ):
            return None
        return self._folder_stack_item.desktop_id

    def _new_popup_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        self._runtime.menu_popup_opened()
        menu.connect("hide", self._on_menu_popup_closed)
        menu.connect("deactivate", self._on_menu_popup_closed)
        return menu

    def _on_menu_popup_closed(self, _menu: Gtk.Menu) -> None:
        self._cleanup_folder_menu_tree(_menu)
        self._runtime.menu_popup_closed()

    def _close_folder_stack(self) -> None:
        window = self._folder_stack_window
        if window is None or not window.get_visible():
            return
        revealer = self._folder_stack_revealer
        if revealer is not None:
            revealer.set_reveal_child(False)
        window.hide()
        self._cleanup_folder_stack()
        self._runtime.menu_popup_closed()

    def _cleanup_folder_stack(self) -> None:
        if self._folder_stack_refresh_source:
            GLib.source_remove(self._folder_stack_refresh_source)
            self._folder_stack_refresh_source = 0
        if self._folder_stack_anim_source:
            GLib.source_remove(self._folder_stack_anim_source)
            self._folder_stack_anim_source = 0
        if self._folder_stack_monitor is not None:
            self._folder_stack_monitor.cancel()
            self._folder_stack_monitor = None
        self._folder_stack_area = None
        self._folder_stack_item = None
        self._folder_stack_anchor_x = 0
        self._folder_stack_anchor_y = 0
        self._folder_stack_icon_w = 0
        self._folder_stack_fold_center_x = 0
        self._folder_stack_show_started_us = 0
        self._folder_stack_hover_target = None
        self._folder_stack_hover_values.clear()
        self._folder_stack_pressed_target = None

    def _ensure_folder_stack_window(self) -> Gtk.Window:
        if self._folder_stack_window is not None:
            return self._folder_stack_window

        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_decorated(False)
        window.set_skip_taskbar_hint(True)
        window.set_resizable(False)
        window.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        window.set_app_paintable(True)

        screen = window.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            window.set_visual(visual)

        revealer = Gtk.Revealer()
        revealer.set_transition_type(self._folder_stack_transition_type())
        revealer.set_transition_duration(140)
        revealer.set_reveal_child(False)
        window.add(revealer)

        self._folder_stack_window = window
        self._folder_stack_revealer = revealer
        return window

    def _folder_stack_transition_type(self):
        pos = self._config.pos
        if pos == "bottom":
            return Gtk.RevealerTransitionType.SLIDE_UP
        if pos == "top":
            return Gtk.RevealerTransitionType.SLIDE_DOWN
        if pos == "left":
            return Gtk.RevealerTransitionType.SLIDE_RIGHT
        return Gtk.RevealerTransitionType.SLIDE_LEFT

    def _replace_folder_stack_content(self, item: DockItem) -> None:
        revealer = self._folder_stack_revealer
        if revealer is None:
            return
        child = revealer.get_child()
        if child is not None:
            revealer.remove(child)
        content = self._build_folder_stack_content(item=item)
        revealer.add(content)
        content.show_all()

    def _position_folder_stack_window(self) -> None:
        window = self._folder_stack_window
        revealer = self._folder_stack_revealer
        if window is None or revealer is None:
            return
        child = revealer.get_child()
        if child is None:
            return

        preferred = child.get_preferred_size()[1]
        popup_w = max(int(preferred.width), 1)
        popup_h = max(int(preferred.height), 1)
        anchor_x = self._folder_stack_anchor_x
        anchor_y = self._folder_stack_anchor_y
        icon_w = max(self._folder_stack_icon_w, 1)
        pos = self._folder_stack_position_value
        local_icon_center_x = max(self._folder_stack_fold_center_x, 1)

        if pos == "bottom":
            popup_x = int(anchor_x + icon_w / 2 - local_icon_center_x)
            popup_y = int(anchor_y - popup_h - FOLDER_STACK_GAP_PX)
        elif pos == "top":
            popup_x = int(anchor_x + icon_w / 2 - local_icon_center_x)
            popup_y = int(anchor_y + FOLDER_STACK_GAP_PX)
        elif pos == "left":
            popup_x = int(anchor_x + FOLDER_STACK_GAP_PX)
            popup_y = int(anchor_y + icon_w / 2 - popup_h / 2)
        else:
            popup_x = int(anchor_x - popup_w - FOLDER_STACK_GAP_PX)
            popup_y = int(anchor_y + icon_w / 2 - popup_h / 2)

        screen = window.get_screen()
        screen_w = screen.get_width()
        screen_h = screen.get_height()
        popup_pos = clamp_to_screen(
            popup_x,
            popup_y,
            popup_w,
            popup_h,
            screen_w,
            screen_h,
        )
        window.move(popup_pos.x, popup_pos.y)

    def _track_folder_stack(self, target: str) -> None:
        uri = launcher_mod.normalize_file_target(target)
        if uri is None:
            return
        try:
            folder = Gio.File.new_for_uri(uri)
            monitor = folder.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            monitor.connect("changed", self._on_folder_stack_changed)
            self._folder_stack_monitor = monitor
        except GLib.Error as exc:
            log.warning("Failed to monitor folder stack target %s: %s", target, exc)

    def _on_folder_stack_changed(
        self,
        _monitor: Gio.FileMonitor,
        _file: Gio.File,
        _other_file: Gio.File | None,
        _event_type: Gio.FileMonitorEvent,
    ) -> None:
        if self._folder_stack_refresh_source:
            GLib.source_remove(self._folder_stack_refresh_source)
        self._folder_stack_refresh_source = GLib.timeout_add(
            FOLDER_MENU_REFRESH_DEBOUNCE_MS,
            self._refresh_folder_stack,
        )

    def _refresh_folder_stack(self) -> bool:
        self._folder_stack_refresh_source = 0
        window = self._folder_stack_window
        item = self._folder_stack_item
        if window is None or item is None:
            return False
        self._replace_folder_stack_content(item=item)
        self._restart_folder_stack_animation()
        self._position_folder_stack_window()
        window.show_all()
        return False

    def _build_folder_stack_content(self, item: DockItem) -> Gtk.Widget:
        cards, popup_w, popup_h = self._folder_stack_cards_for_item(item)
        self._folder_stack_cards = cards

        area = Gtk.DrawingArea()
        area.set_size_request(popup_w, popup_h)
        area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        area.connect("draw", self._on_folder_stack_draw)
        area.connect("button-press-event", self._on_folder_stack_button_press)
        area.connect("button-release-event", self._on_folder_stack_button_release)
        area.connect("motion-notify-event", self._on_folder_stack_motion_notify)
        area.connect("leave-notify-event", self._on_folder_stack_leave_notify)
        self._folder_stack_area = area
        return area

    def _folder_stack_cards_for_item(
        self, item: DockItem
    ) -> tuple[list[FolderStackCard], int, int]:
        cards: list[FolderStackCard] = []
        icon_px = max(int(self._config.icon_size), 1)
        label_h = FOLDER_STACK_LABEL_HEIGHT_PX
        row_step = max(FOLDER_STACK_ROW_STEP_PX, round(icon_px * 1.08))
        curve_extent = max(FOLDER_STACK_CURVE_X_PX, round(icon_px * 0.65))
        right_bleed = max(
            FOLDER_STACK_RIGHT_BLEED_PX,
            round(curve_extent + icon_px * FOLDER_STACK_HOVER_SCALE * 0.35),
        )
        fold_center_x = int(
            FOLDER_STACK_POPUP_SIDE_PADDING_PX
            + FOLDER_STACK_LABEL_MAX_WIDTH_PX
            + FOLDER_STACK_ICON_GAP_PX
            + icon_px / 2
        )
        self._folder_stack_fold_center_x = fold_center_x

        state = self._folder_target_state(item.target)
        if state == "missing":
            label_w = 190
            cards.append(
                FolderStackCard(
                    label=_("Folder not found"),
                    target=None,
                    icon=None,
                    icon_x=0,
                    icon_y=0,
                    icon_size=0,
                    label_x=max(
                        FOLDER_STACK_POPUP_SIDE_PADDING_PX,
                        int(fold_center_x - label_w / 2),
                    ),
                    label_y=FOLDER_STACK_TOP_PADDING_PX,
                    label_w=label_w,
                    label_h=label_h,
                    centered=True,
                )
            )
            popup_w = int(
                max(
                    fold_center_x + label_w / 2 + FOLDER_STACK_POPUP_SIDE_PADDING_PX,
                    fold_center_x + icon_px / 2 + right_bleed,
                )
            )
            popup_h = label_h + 2 * FOLDER_STACK_TOP_PADDING_PX
            return cards, popup_w, popup_h

        rows = self._list_directory(
            folder_item=item,
            target=item.target,
            icon_px=icon_px,
        )
        if not rows:
            label_w = 190
            cards.append(
                FolderStackCard(
                    label=_("Folder is empty"),
                    target=None,
                    icon=None,
                    icon_x=0,
                    icon_y=0,
                    icon_size=0,
                    label_x=max(
                        FOLDER_STACK_POPUP_SIDE_PADDING_PX,
                        int(fold_center_x - label_w / 2),
                    ),
                    label_y=FOLDER_STACK_TOP_PADDING_PX,
                    label_w=label_w,
                    label_h=label_h,
                    centered=True,
                )
            )
            popup_w = int(
                max(
                    fold_center_x + label_w / 2 + FOLDER_STACK_POPUP_SIDE_PADDING_PX,
                    fold_center_x + icon_px / 2 + right_bleed,
                )
            )
            popup_h = label_h + 2 * FOLDER_STACK_TOP_PADDING_PX
            return cards, popup_w, popup_h

        visible_rows = rows[:FOLDER_STACK_MAX_VISIBLE_ROWS]
        hidden_count = max(len(rows) - len(visible_rows), 0)
        action_label = self._folder_stack_action_label(hidden_count=hidden_count)
        chip_w = self._folder_stack_action_width(label=action_label)
        chip_h = label_h
        total_rows = len(visible_rows)
        top_progress = 1.0 if total_rows > 0 else 0.0
        total_span = (total_rows - 1) * row_step
        top_center_x = round(
            fold_center_x + _folder_stack_arc_offset(top_progress, total_span)
        )
        chip_x = max(
            FOLDER_STACK_POPUP_SIDE_PADDING_PX,
            int(top_center_x - chip_w / 2 + curve_extent * 0.1),
        )
        chip_y = FOLDER_STACK_TOP_PADDING_PX
        cards.append(
            FolderStackCard(
                label=action_label,
                target=item.target,
                icon=None,
                icon_x=0,
                icon_y=0,
                icon_size=0,
                label_x=chip_x,
                label_y=chip_y,
                label_w=chip_w,
                label_h=chip_h,
                centered=True,
                stack_progress=1.0,
                arc_span=float(total_span),
            )
        )

        stack_top = chip_y + chip_h + FOLDER_STACK_ACTION_GAP_PX
        max_right = chip_x + chip_w
        bottom_center_y = (
            stack_top + (total_rows - 1) * row_step + icon_px / 2 if total_rows else 0
        )
        for index, child in enumerate(visible_rows):
            raw_progress = (
                (total_rows - 1 - index) / max(total_rows - 1, 1)
                if total_rows > 1
                else 1.0
            )
            arc_progress = raw_progress
            icon_center_x = fold_center_x + _folder_stack_arc_offset(
                arc_progress,
                total_span,
            )
            icon_center_y = bottom_center_y - total_span * raw_progress
            icon_x = round(icon_center_x - icon_px / 2)
            icon_y = round(icon_center_y - icon_px / 2)
            label_w = self._folder_stack_label_width(label=str(child["name"]))
            label_pull = round(arc_progress * 10)
            label_x = max(
                FOLDER_STACK_POPUP_SIDE_PADDING_PX,
                icon_x - FOLDER_STACK_ICON_GAP_PX - label_w - label_pull,
            )
            cards.append(
                FolderStackCard(
                    label=str(child["name"]),
                    target=str(child["target"]),
                    icon=child["icon"],
                    icon_x=icon_x,
                    icon_y=icon_y,
                    icon_size=icon_px,
                    label_x=label_x,
                    label_y=icon_y + max(int((icon_px - label_h) / 2), 0),
                    label_w=label_w,
                    label_h=label_h,
                    centered=False,
                    stack_progress=arc_progress,
                    arc_span=float(total_span),
                )
            )
            max_right = max(max_right, icon_x + icon_px)

        popup_w = int(
            max(
                max_right + right_bleed,
                fold_center_x
                + _folder_stack_arc_offset(1.0, total_span)
                + icon_px / 2
                + right_bleed,
            )
        )
        popup_h = (
            stack_top
            + (total_rows - 1) * row_step
            + icon_px
            + FOLDER_STACK_TOP_PADDING_PX
        )
        return cards, popup_w, popup_h

    def _on_folder_stack_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        now_us = GLib.get_monotonic_time()
        total_cards = len(self._folder_stack_cards)
        for draw_index, card in enumerate(self._folder_stack_cards):
            self._draw_folder_stack_card(
                cr=cr,
                card=card,
                sequence_index=total_cards - 1 - draw_index,
                now_us=now_us,
            )
        return False

    def _folder_stack_card_geometry(
        self,
        *,
        card: FolderStackCard,
        sequence_index: int,
        now_us: int,
    ) -> FolderStackCardGeometry | None:
        reveal = self._folder_stack_reveal_progress(
            sequence_index=sequence_index,
            now_us=now_us,
        )
        if reveal <= 0:
            return None

        hover_value = (
            self._folder_stack_hover_values.get(card.target, 0.0)
            if card.target is not None and not card.centered
            else 0.0
        )
        y_offset = (1.0 - reveal) * 18.0
        rotation_radians = (
            _folder_stack_rotation(
                card.stack_progress,
                self._folder_stack_position_value,
                card.arc_span,
            )
            * reveal
        )
        open_label_center_x = card.label_x + card.label_w / 2
        label_center_x = (
            self._folder_stack_fold_center_x
            + (open_label_center_x - self._folder_stack_fold_center_x) * reveal
        )
        label_x = label_center_x - card.label_w / 2
        label_y = card.label_y + y_offset

        icon_size = 0.0
        icon_x = 0.0
        icon_y = 0.0
        icon_center_x = 0.0
        icon_center_y = 0.0
        if card.icon is not None and card.icon_size > 0:
            icon_size = max(
                (
                    card.icon_size
                    * (0.82 + 0.18 * reveal)
                    * (1.0 + hover_value * (FOLDER_STACK_HOVER_SCALE - 1.0))
                ),
                1.0,
            )
            open_icon_center_x = card.icon_x + card.icon_size / 2
            icon_center_x = (
                self._folder_stack_fold_center_x
                + (open_icon_center_x - self._folder_stack_fold_center_x) * reveal
            )
            icon_center_y = (
                card.icon_y + card.icon_size / 2 + y_offset - hover_value * 4.0
            )
            icon_x = icon_center_x - icon_size / 2
            icon_y = icon_center_y - icon_size / 2

        return FolderStackCardGeometry(
            reveal=reveal,
            hover_value=hover_value,
            rotation_radians=rotation_radians,
            icon_x=icon_x,
            icon_y=icon_y,
            icon_size=icon_size,
            icon_center_x=icon_center_x,
            icon_center_y=icon_center_y,
            label_x=label_x,
            label_y=label_y,
        )

    def _draw_folder_stack_card(
        self,
        *,
        cr: cairo.Context,
        card: FolderStackCard,
        sequence_index: int,
        now_us: int,
    ) -> None:
        geometry = self._folder_stack_card_geometry(
            card=card,
            sequence_index=sequence_index,
            now_us=now_us,
        )
        if geometry is None:
            return
        is_action_card = _is_folder_stack_action_card(card)

        if card.icon is not None and card.icon_size > 0:
            pixbuf = card.icon
            draw_icon_size = max(round(geometry.icon_size), 1)
            if (
                pixbuf.get_width() != draw_icon_size
                or pixbuf.get_height() != draw_icon_size
            ):
                scaled = pixbuf.scale_simple(
                    draw_icon_size,
                    draw_icon_size,
                    GdkPixbuf.InterpType.BILINEAR,
                )
                if scaled is not None:
                    pixbuf = scaled

            cr.save()
            cr.translate(geometry.icon_center_x + 2, geometry.icon_center_y + 2)
            cr.rotate(geometry.rotation_radians)
            Gdk.cairo_set_source_pixbuf(
                cr,
                pixbuf,
                -draw_icon_size / 2,
                -draw_icon_size / 2,
            )
            cr.paint_with_alpha(0.16 * geometry.reveal)
            cr.restore()

            cr.save()
            cr.translate(geometry.icon_center_x, geometry.icon_center_y)
            cr.rotate(geometry.rotation_radians)
            Gdk.cairo_set_source_pixbuf(
                cr,
                pixbuf,
                -draw_icon_size / 2,
                -draw_icon_size / 2,
            )
            cr.paint_with_alpha(0.55 + 0.45 * geometry.reveal)
            cr.restore()

        radius = FOLDER_STACK_LABEL_RADIUS_PX
        label_center_x = geometry.label_x + card.label_w / 2
        label_center_y = geometry.label_y + card.label_h / 2
        cr.save()
        cr.translate(label_center_x, label_center_y + 1)
        cr.rotate(geometry.rotation_radians * 0.85)
        rounded_rect(
            cr,
            -card.label_w / 2,
            -card.label_h / 2,
            card.label_w,
            card.label_h,
            radius,
        )
        cr.set_source_rgba(0, 0, 0, 0.08 * geometry.reveal)
        cr.fill()
        cr.restore()

        cr.save()
        cr.translate(label_center_x, label_center_y)
        cr.rotate(geometry.rotation_radians * 0.85)
        rounded_rect(
            cr,
            -card.label_w / 2,
            -card.label_h / 2,
            card.label_w,
            card.label_h,
            radius,
        )
        cr.set_source_rgba(0.98, 0.98, 0.98, 0.95)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.08)
        cr.set_line_width(1.0)
        cr.stroke()
        cr.restore()

        cr.save()
        layout = PangoCairo.create_layout(cr)
        layout.set_text(card.label, -1)
        desc = Pango.FontDescription()
        desc.set_family("Sans")
        desc.set_size(10 * Pango.SCALE)
        layout.set_font_description(desc)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        arrow_reserve = (
            FOLDER_STACK_ACTION_ARROW_GAP_PX + FOLDER_STACK_ACTION_ARROW_SIZE_PX
            if is_action_card
            else 0
        )
        available_text_w = max(
            int(card.label_w - 2 * FOLDER_STACK_LABEL_TEXT_MARGIN_PX - arrow_reserve),
            1,
        )
        layout.set_width(available_text_w * Pango.SCALE)
        layout.set_alignment(Pango.Alignment.CENTER)
        _, logical = layout.get_pixel_extents()
        text_y = int(-card.label_h / 2 + (card.label_h - logical.height) / 2)
        text_x = -card.label_w / 2 + FOLDER_STACK_LABEL_TEXT_MARGIN_PX
        cr.set_source_rgba(0.16, 0.2, 0.26, 1.0)
        cr.translate(label_center_x, label_center_y)
        cr.rotate(geometry.rotation_radians * 0.85)
        cr.move_to(text_x, text_y)
        PangoCairo.show_layout(cr, layout)
        if is_action_card:
            arrow_center_x = (
                card.label_w / 2
                - FOLDER_STACK_LABEL_TEXT_MARGIN_PX
                - FOLDER_STACK_ACTION_ARROW_SIZE_PX / 2
            )
            arrow_center_y = 0.0
            half = FOLDER_STACK_ACTION_ARROW_SIZE_PX / 2
            cr.set_line_width(1.4)
            cr.set_line_cap(cairo.LineCap.ROUND)
            cr.set_line_join(cairo.LineJoin.ROUND)
            cr.move_to(arrow_center_x - half, arrow_center_y - half)
            cr.line_to(arrow_center_x, arrow_center_y)
            cr.line_to(arrow_center_x - half, arrow_center_y + half)
            cr.stroke()
        cr.restore()

    def _folder_stack_card_at(self, x: float, y: float) -> FolderStackCard | None:
        now_us = GLib.get_monotonic_time()
        total_cards = len(self._folder_stack_cards)
        for index in range(total_cards - 1, -1, -1):
            card = self._folder_stack_cards[index]
            geometry = self._folder_stack_card_geometry(
                card=card,
                sequence_index=total_cards - 1 - index,
                now_us=now_us,
            )
            if geometry is None:
                continue
            within_label = (
                geometry.label_x <= x <= geometry.label_x + card.label_w
                and geometry.label_y <= y <= geometry.label_y + card.label_h
            )
            within_icon = (
                geometry.icon_size > 0
                and geometry.icon_x <= x <= geometry.icon_x + geometry.icon_size
                and geometry.icon_y <= y <= geometry.icon_y + geometry.icon_size
            )
            if within_label or within_icon:
                return card
        return None

    def _on_folder_stack_button_press(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        if int(event.button) != 1:
            self._folder_stack_pressed_target = None
            return False
        card = self._folder_stack_card_at(event.x, event.y)
        self._folder_stack_pressed_target = (
            card.target if card is not None and card.target is not None else None
        )
        return self._folder_stack_pressed_target is not None

    def _on_folder_stack_button_release(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        if int(event.button) != 1:
            self._folder_stack_pressed_target = None
            return False
        card = self._folder_stack_card_at(event.x, event.y)
        target = card.target if card is not None and card.target is not None else None
        pressed_target = self._folder_stack_pressed_target
        self._folder_stack_pressed_target = None
        if target is not None and (pressed_target is None or pressed_target == target):
            self._open_folder_stack_target(target)
            return True
        return False

    def _on_folder_stack_motion_notify(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventMotion
    ) -> bool:
        card = self._folder_stack_card_at(event.x, event.y)
        target = (
            card.target
            if card is not None and card.target is not None and not card.centered
            else None
        )
        if target != self._folder_stack_hover_target:
            self._folder_stack_hover_target = target
            self._ensure_folder_stack_animating()
        return False

    def _on_folder_stack_leave_notify(
        self, _widget: Gtk.DrawingArea, _event: Gdk.EventCrossing
    ) -> bool:
        if self._folder_stack_hover_target is not None:
            self._folder_stack_hover_target = None
            self._ensure_folder_stack_animating()
        self._folder_stack_pressed_target = None
        return False

    def _folder_stack_action_label(self, *, hidden_count: int) -> str:
        app_name = (
            self._launcher.default_directory_app_name()
            if self._launcher is not None
            else None
        )
        if app_name:
            return (
                _("Open in %s") % app_name
                if hidden_count == 0
                else _("%d More in %s") % (hidden_count, app_name)
            )
        return (
            _("Open Folder")
            if hidden_count == 0
            else _("%d More in Folder") % hidden_count
        )

    def _folder_stack_action_width(self, *, label: str) -> int:
        return min(
            FOLDER_STACK_ACTION_MAX_WIDTH_PX,
            _measure_stack_text_px(label)
            + 2 * FOLDER_STACK_LABEL_TEXT_MARGIN_PX
            + FOLDER_STACK_ACTION_ARROW_GAP_PX
            + FOLDER_STACK_ACTION_ARROW_SIZE_PX
            + 10,
        )

    def _folder_stack_label_width(self, *, label: str) -> int:
        return min(
            FOLDER_STACK_LABEL_MAX_WIDTH_PX,
            max(
                24,
                _measure_stack_text_px(label)
                + 2 * FOLDER_STACK_LABEL_TEXT_MARGIN_PX
                + 10,
            ),
        )

    def _restart_folder_stack_animation(self) -> None:
        self._folder_stack_show_started_us = GLib.get_monotonic_time()
        self._folder_stack_hover_target = None
        self._folder_stack_hover_values.clear()
        self._ensure_folder_stack_animating()

    def _ensure_folder_stack_animating(self) -> None:
        area = self._folder_stack_area
        if area is None:
            return
        area.queue_draw()
        if self._folder_stack_anim_source == 0:
            self._folder_stack_anim_source = GLib.timeout_add(
                FOLDER_STACK_ANIM_FRAME_MS,
                self._on_folder_stack_animation_frame,
            )

    def _on_folder_stack_animation_frame(self) -> bool:
        area = self._folder_stack_area
        window = self._folder_stack_window
        if area is None or window is None or not window.get_visible():
            self._folder_stack_anim_source = 0
            return False

        active = False
        now_us = GLib.get_monotonic_time()
        elapsed_ms = max((now_us - self._folder_stack_show_started_us) / 1000.0, 0.0)
        reveal_budget_ms = (
            FOLDER_STACK_REVEAL_DURATION_MS
            + max(
                len(self._folder_stack_cards) - 1,
                0,
            )
            * FOLDER_STACK_REVEAL_STAGGER_MS
        )
        if elapsed_ms < reveal_budget_ms:
            active = True

        for card in self._folder_stack_cards:
            if card.target is None or card.centered:
                continue
            current = self._folder_stack_hover_values.get(card.target, 0.0)
            target = 1.0 if self._folder_stack_hover_target == card.target else 0.0
            updated = current + (target - current) * FOLDER_STACK_HOVER_EASE
            if abs(updated - target) < 0.02:
                updated = target
            if updated <= 0.0 and target == 0.0:
                self._folder_stack_hover_values.pop(card.target, None)
            else:
                self._folder_stack_hover_values[card.target] = updated
            if updated != target:
                active = True

        area.queue_draw()
        if not active:
            self._folder_stack_anim_source = 0
            return False
        return True

    def _folder_stack_reveal_progress(
        self, *, sequence_index: int, now_us: int
    ) -> float:
        if self._folder_stack_show_started_us <= 0:
            return 1.0
        elapsed_ms = max((now_us - self._folder_stack_show_started_us) / 1000.0, 0.0)
        elapsed_ms -= sequence_index * FOLDER_STACK_REVEAL_STAGGER_MS
        if elapsed_ms <= 0:
            return 0.0
        return _ease_out_cubic(elapsed_ms / FOLDER_STACK_REVEAL_DURATION_MS)

    def _open_folder_stack_target(self, target: str) -> None:
        launcher_mod.open_target(target)
        self._close_folder_stack()

    def _folder_target_state(self, target: str) -> str:
        uri = launcher_mod.normalize_file_target(target)
        if uri is None:
            return "missing"
        try:
            folder = Gio.File.new_for_uri(uri)
            return "ok" if folder.query_exists(None) else "missing"
        except Exception as exc:
            log.debug("Failed to query folder target %s: %s", target, exc)
            return "missing"

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

        # Open windows - click to activate
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

        Sections: add actions plus preferences/about/quit.
        """
        # Add Applet submenu
        try:
            catalog = get_applet_catalog()
        except Exception as exc:
            log.warning("Failed to read applet catalog for add-applet menu: %s", exc)
            catalog = {}
        active_ids = {
            item.desktop_id
            for item in self._model.pinned_items
            if is_applet(desktop_id=item.desktop_id)
        }
        add_applet = Gtk.MenuItem(label=_("Add Applet"))
        add_applet_menu = Gtk.Menu()
        grouped: dict[AppletCategory, list[tuple[str, AppletMeta]]] = {
            category: [] for category in APPLET_CATEGORY_ORDER
        }
        for did, entry in sorted(catalog.items(), key=lambda item: str(item[0])):
            if did == _separator_meta.id:
                continue
            desktop_id = applet_desktop_id(applet_id=did)
            if desktop_id in active_ids:
                continue
            grouped[entry.category].append((did, entry))

        non_empty_categories = [
            key for key in APPLET_CATEGORY_ORDER if grouped.get(key)
        ]
        if non_empty_categories:
            for i, category in enumerate(non_empty_categories):
                add_applet_menu.append(_make_menu_header(label=_(category.value)))
                for did, entry in sorted(
                    grouped[category], key=lambda item: item[1].name.lower()
                ):
                    item = Gtk.MenuItem(label=entry.name)
                    pixbuf: GdkPixbuf.Pixbuf | None = load_catalog_icon(
                        applet_id=did,
                        size=APPLET_MENU_ICON_PX,
                    )
                    _set_menu_item_icon(
                        item=item,
                        label=entry.name,
                        pixbuf=pixbuf,
                        icon_px=APPLET_MENU_ICON_PX,
                    )
                    item.connect("activate", self._on_add_applet_activate, str(did))
                    add_applet_menu.append(item)
                if i < len(non_empty_categories) - 1:
                    add_applet_menu.append(Gtk.SeparatorMenuItem())
        else:
            empty = Gtk.MenuItem(label=_("No Applets Available"))
            empty.set_sensitive(False)
            add_applet_menu.append(empty)
        add_applet.set_submenu(add_applet_menu)
        menu.append(add_applet)

        # Add Separator (multi-instance, not a toggle)
        add_sep = Gtk.MenuItem(label=_("Add Separator"))
        add_sep.connect(
            "activate",
            lambda _, idx=insert_index: self._model.add_separator(index=idx),
        )
        menu.append(add_sep)

        menu.append(Gtk.SeparatorMenuItem())

        # Preferences
        prefs_item = Gtk.MenuItem(label=_("Preferences"))
        prefs_item.connect("activate", lambda _: self._settings.show())
        menu.append(prefs_item)

        # About
        about_item = Gtk.MenuItem(label=_("About"))
        about_item.connect("activate", lambda _: self._about.show())
        menu.append(about_item)

        # Support
        support_item = Gtk.MenuItem(label=_("Get Support"))
        support_item.connect(
            "activate", lambda _: launcher_mod.open_target(SUPPORT_URL)
        )
        menu.append(support_item)

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
        separator._window_rows_separator = True
        menu.append(separator)

    def _build_window_menu_row(self, window: Any) -> Gtk.MenuItem:
        xid = window.get_xid()
        title = self._tracker.get_window_title_for_xid(xid) or _("Window")
        row = Gtk.MenuItem()
        row.set_label(title)
        thumb = capture_window(
            wnck_window=window, thumb_w=WINDOW_MENU_THUMB_W, thumb_h=WINDOW_MENU_THUMB_H
        )

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=MENU_ROW_SPACING_PX,
        )
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
        close_label.set_xalign(WINDOW_MENU_CLOSE_LABEL_XALIGN)
        close_label.set_margin_end(WINDOW_MENU_CLOSE_MARGIN_END_PX)
        box.pack_end(close_label, False, False, 0)

        child = row.get_child()
        if child is not None:
            row.remove(child)
        row.add(box)
        row._window_row = True
        row.connect("button-press-event", self._on_window_row_button_press, xid)
        row.connect("button-release-event", self._on_window_row_button_release, xid)
        row.connect("activate", lambda *_a: self._tracker.activate_xid(xid))
        return row

    def _window_close_zone_hit(
        self, widget: Gtk.Widget, event: Gdk.EventButton
    ) -> bool:
        x = float(event.x)
        if x < 0:
            return False
        alloc = widget.get_allocation()
        width = float(alloc.width)
        return width > 0 and x >= max(0.0, width - WINDOW_MENU_CLOSE_HIT_W)

    def _on_window_row_button_press(
        self, widget: Gtk.Widget, event: Gdk.EventButton, xid: int
    ) -> bool:
        return self._window_close_zone_hit(widget=widget, event=event)

    def _on_window_row_button_release(
        self, widget: Gtk.Widget, event: Gdk.EventButton, xid: int
    ) -> bool:
        if not self._window_close_zone_hit(widget=widget, event=event):
            return False
        self._tracker.close_xid(xid)
        self._remove_window_row(widget=widget, event=event)
        self._runtime.hide_hover_ui()
        return True

    def _remove_window_row(
        self, widget: Gtk.Widget, event: Gdk.EventButton | None = None
    ) -> None:
        parent = widget.get_parent()
        if parent is None or not isinstance(parent, Gtk.Menu):
            return
        widget.hide()
        parent.remove(widget)
        widget.destroy()
        children = list(parent.get_children())
        if not any(getattr(child, "_window_row", False) for child in children):
            for child in children:
                if getattr(child, "_window_rows_separator", False):
                    child.hide()
                    parent.remove(child)
                    child.destroy()
                    break
        parent.popdown()
        parent.show_all()
        parent.queue_resize()
        parent.check_resize()
        parent.queue_draw()
        parent.popup_at_pointer(event)

    def _on_add_applet_activate(self, _widget: Gtk.MenuItem, applet_id: str) -> None:
        self._model.add_applet(applet_id)

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
        self._runtime.queue_draw()

    def _populate_directory_menu(
        self, menu: Gtk.Menu, folder_item: DockItem, target: str
    ) -> None:
        rows = self._list_directory(folder_item=folder_item, target=target)
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
        except GLib.Error as exc:
            log.warning("Failed to monitor folder menu target %s: %s", target, exc)
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
        source = GLib.timeout_add(
            FOLDER_MENU_REFRESH_DEBOUNCE_MS,
            self._refresh_folder_menu,
            menu_id,
        )
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
            submenu = child.get_submenu() if isinstance(child, Gtk.MenuItem) else None
            if submenu is not None:
                self._cleanup_folder_menu_tree(submenu)
            menu.remove(child)

    def _cleanup_folder_menu_tree(self, menu: Gtk.Menu) -> None:
        for child in list(menu.get_children()):
            submenu = child.get_submenu() if isinstance(child, Gtk.MenuItem) else None
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
        self,
        folder_item: DockItem,
        target: str,
        icon_px: int | None = None,
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
                        "standard::content-type",
                        "standard::is-hidden",
                        "standard::size",
                        "time::created",
                        "time::modified",
                    )
                ),
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except Exception as exc:
            log.warning("Failed to enumerate folder menu target %s: %s", target, exc)
            return []

        prefs = self._folder_prefs(folder_item)
        resolved_icon_px = (
            self._folder_icon_px(folder_item=folder_item)
            if icon_px is None
            else max(int(icon_px), 1)
        )
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
                        self._launcher.resolve_file_icon(
                            target=child_uri,
                            gicon=icon,
                            content_type=info.get_content_type() or "",
                            size=resolved_icon_px,
                            is_dir=is_dir,
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
        except Exception as exc:
            log.warning(
                "Failed to inspect folder children for target %s: %s",
                target,
                exc,
            )
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
            return FOLDER_LARGE_ICON_PX
        return FOLDER_SMALL_ICON_PX

    def _update_folder_pref(self, item: DockItem, key: str, value: Any) -> None:
        prefs = self._folder_prefs(item)
        prefs[key] = value
        self._save_folder_prefs(item, prefs)

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
