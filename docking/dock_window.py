"""Main dock window — GTK window with X11 dock hints, struts, and event wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkX11  # noqa: E402

if TYPE_CHECKING:
    from docking.config import Config
    from docking.dock_model import DockModel
    from docking.dock_renderer import DockRenderer
    from docking.theme import Theme
    from docking.autohide import AutoHideController
    from docking.window_tracker import WindowTracker
    from docking.dnd import DnDHandler
    from docking.menu import MenuHandler


class DockWindow(Gtk.Window):
    """Dock window positioned at screen bottom with X11 DOCK type hints."""

    def __init__(
        self,
        config: Config,
        model: DockModel,
        renderer: DockRenderer,
        theme: Theme,
        window_tracker: WindowTracker,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.config = config
        self.model = model
        self.renderer = renderer
        self.theme = theme
        self.window_tracker = window_tracker
        self.cursor_x: float = -1.0
        self.cursor_y: float = -1.0
        self._autohide: AutoHideController | None = None
        self._dnd: DnDHandler | None = None
        self._menu: MenuHandler | None = None

        self._setup_window()
        self._setup_drawing_area()
        self._connect_model()

    def _setup_window(self) -> None:
        """Configure window as an X11 dock."""
        self.set_title("Docking")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.stick()
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_app_paintable(True)

        # Enable RGBA visual for transparency
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self.connect("realize", self._on_realize)
        self.connect("destroy", Gtk.main_quit)

    def _setup_drawing_area(self) -> None:
        """Create the drawing surface and wire events."""
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.drawing_area.connect("draw", self._on_draw)
        self.drawing_area.connect("motion-notify-event", self._on_motion)
        self.drawing_area.connect("button-press-event", self._on_button_press)
        self.drawing_area.connect("leave-notify-event", self._on_leave)
        self.drawing_area.connect("enter-notify-event", self._on_enter)
        self.add(self.drawing_area)

    def _connect_model(self) -> None:
        """Listen for model changes to trigger redraws."""
        self.model.on_change = self._on_model_changed

    def set_autohide_controller(self, controller: AutoHideController) -> None:
        self._autohide = controller

    def set_dnd_handler(self, handler: DnDHandler) -> None:
        self._dnd = handler

    def set_menu_handler(self, handler: MenuHandler) -> None:
        self._menu = handler

    def _on_realize(self, widget: Gtk.Widget) -> None:
        """Position dock and set struts after window is realized."""
        self._position_dock()
        self._set_struts()

    def _position_dock(self) -> None:
        """Center dock at the bottom of the primary monitor."""
        dock_width, dock_height = self.renderer.compute_dock_size(
            self.model, self.config, self.theme
        )
        self.set_size_request(dock_width, dock_height)
        self.resize(dock_width, dock_height)

        display = self.get_display()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geom = monitor.get_geometry()

        x = geom.x + (geom.width - dock_width) // 2
        y = geom.y + geom.height - dock_height
        self.move(x, y)

    def _set_struts(self) -> None:
        """Reserve screen space for the dock via _NET_WM_STRUT_PARTIAL."""
        if self.config.autohide:
            self._clear_struts()
            return

        gdk_window = self.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return

        display = self.get_display()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geom = monitor.get_geometry()
        screen = self.get_screen()
        scale = gdk_window.get_scale_factor()

        _, dock_height = self.renderer.compute_dock_size(
            self.model, self.config, self.theme
        )

        screen_h = screen.get_height()
        bottom = (dock_height + screen_h - geom.y - geom.height) * scale
        bottom_start = geom.x * scale
        bottom_end = (geom.x + geom.width) * scale - 1

        # _NET_WM_STRUT_PARTIAL: left, right, top, bottom,
        #   left_start, left_end, right_start, right_end,
        #   top_start, top_end, bottom_start, bottom_end
        struts = [0, 0, 0, int(bottom), 0, 0, 0, 0, 0, 0, int(bottom_start), int(bottom_end)]
        self._xprop_set_struts(gdk_window, struts)

    @staticmethod
    def _xprop_set_struts(gdk_window: GdkX11.X11Window, struts: list[int]) -> None:
        """Set _NET_WM_STRUT and _NET_WM_STRUT_PARTIAL via ctypes/Xlib."""
        import ctypes

        xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
        xid = gdk_window.get_xid()
        xdisplay = ctypes.c_void_p(hash(
            GdkX11.X11Display.get_default().get_xdisplay()
        ))

        xlib.XInternAtom.restype = ctypes.c_ulong
        xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]

        atom_partial = xlib.XInternAtom(xdisplay, b"_NET_WM_STRUT_PARTIAL", 0)
        atom_strut = xlib.XInternAtom(xdisplay, b"_NET_WM_STRUT", 0)
        xa_cardinal = xlib.XInternAtom(xdisplay, b"CARDINAL", 0)

        xlib.XChangeProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int,
        ]

        arr12 = (ctypes.c_long * 12)(*struts)
        arr4 = (ctypes.c_long * 4)(*struts[:4])

        xlib.XChangeProperty(xdisplay, xid, atom_partial, xa_cardinal, 32, 0, ctypes.byref(arr12), 12)
        xlib.XChangeProperty(xdisplay, xid, atom_strut, xa_cardinal, 32, 0, ctypes.byref(arr4), 4)
        xlib.XFlush(xdisplay)

    def _clear_struts(self) -> None:
        """Remove strut reservation by setting all struts to zero."""
        gdk_window = self.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return
        self._xprop_set_struts(gdk_window, [0] * 12)

    def _on_draw(self, widget: Gtk.DrawingArea, cr) -> bool:
        """Render the dock via the renderer."""
        hide_offset = self._autohide.hide_offset if self._autohide else 0.0
        drag_index = self._dnd.drag_index if self._dnd else -1
        self.renderer.draw(
            cr, widget, self.model, self.config, self.theme,
            self.cursor_x, hide_offset, drag_index,
        )
        return True

    def _on_motion(self, widget: Gtk.DrawingArea, event: Gdk.EventMotion) -> bool:
        """Update cursor position and trigger zoom redraw."""
        self.cursor_x = event.x
        self.cursor_y = event.y
        self._update_dock_size()
        widget.queue_draw()
        return True

    def _on_button_press(self, widget: Gtk.DrawingArea, event: Gdk.EventButton) -> bool:
        """Handle clicks on dock items."""
        if event.button == 3:
            if self._menu:
                self._menu.show(event, self.cursor_x)
            return True

        if event.button == 1 or event.button == 2:
            from docking.zoom import compute_layout
            layout = compute_layout(
                self.model.visible_items(), self.config, self.cursor_x
            )
            item = self._hit_test(event.x, layout)
            if item is None:
                return True

            force_launch = (
                event.button == 2
                or (event.state & Gdk.ModifierType.CONTROL_MASK)
            )
            if force_launch or not item.is_running:
                from docking.launcher import launch
                launch(item.desktop_id)
            else:
                self.window_tracker.toggle_focus(item.desktop_id)

        return True

    def _on_leave(self, widget: Gtk.DrawingArea, event: Gdk.EventCrossing) -> bool:
        """Reset cursor and notify auto-hide."""
        # Ignore leave events caused by grabs (e.g., menu popup)
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self.cursor_x = -1.0
        self._update_dock_size()
        widget.queue_draw()
        if self._autohide:
            self._autohide.on_mouse_leave()
        return True

    def _on_enter(self, widget: Gtk.DrawingArea, event: Gdk.EventCrossing) -> bool:
        """Notify auto-hide on mouse enter."""
        if self._autohide:
            self._autohide.on_mouse_enter()
        return True

    def _on_model_changed(self) -> None:
        """Reposition and redraw when the model changes."""
        self._update_dock_size()
        self.drawing_area.queue_draw()

    def _update_dock_size(self) -> None:
        """Recalculate dock dimensions and reposition if size changed."""
        from docking.zoom import compute_layout
        items = self.model.visible_items()
        layout = compute_layout(items, self.config, self.cursor_x)
        total_width = self.renderer.compute_zoomed_width(layout, self.config, self.theme)
        _, base_height = self.renderer.compute_dock_size(
            self.model, self.config, self.theme
        )
        zoomed_icon = self.config.icon_size * self.config.zoom_percent if self.config.zoom_enabled else self.config.icon_size
        total_height = int(zoomed_icon + self.theme.top_padding + self.theme.bottom_padding)

        current_w, current_h = self.get_size()
        if total_width != current_w or total_height != current_h:
            self.set_size_request(total_width, total_height)
            self.resize(total_width, total_height)

            display = self.get_display()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            geom = monitor.get_geometry()
            x = geom.x + (geom.width - total_width) // 2
            y = geom.y + geom.height - total_height
            self.move(x, y)

    def _hit_test(self, x: float, layout: list) -> object | None:
        """Find which DockItem is under the cursor x position."""
        from docking.zoom import LayoutItem
        items = self.model.visible_items()
        for i, li in enumerate(layout):
            icon_w = li.scale * self.config.icon_size
            left = li.x
            right = li.x + icon_w
            if left <= x <= right:
                return items[i]
        return None

    def update_position(self) -> None:
        """Public method for auto-hide to reposition the dock."""
        self._position_dock()
        self.drawing_area.queue_draw()

    def queue_redraw(self) -> None:
        """Convenience for external controllers to trigger redraw."""
        self.drawing_area.queue_draw()
