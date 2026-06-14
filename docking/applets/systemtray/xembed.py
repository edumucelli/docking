# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Persistent XEmbed system tray host for legacy X11 tray icons."""

from __future__ import annotations

import ctypes
import ctypes.util
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk

from docking.core.position import Position
from docking.log import get_logger

log = get_logger("systemtray.xembed")

CLIENT_MESSAGE = 33
DESTROY_NOTIFY = 17
SELECTION_CLEAR = 29
SYSTEM_TRAY_REQUEST_DOCK = 0
CURRENT_TIME = 0
STRUCTURE_NOTIFY_MASK = 1 << 17
GDK_FILTER_CONTINUE = 0
GDK_FILTER_REMOVE = 1
PROP_MODE_REPLACE = 0


@dataclass(frozen=True, slots=True)
class LegacyTrayIcon:
    """One XEmbed tray icon currently docked into Docking."""

    xid: int
    title: str


class XClientMessageData(ctypes.Union):
    _fields_: ClassVar = [
        ("b", ctypes.c_char * 20),
        ("s", ctypes.c_short * 10),
        ("l", ctypes.c_long * 5),
    ]


class XClientMessageEvent(ctypes.Structure):
    _fields_: ClassVar = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", XClientMessageData),
    ]


class XDestroyWindowEvent(ctypes.Structure):
    _fields_: ClassVar = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("event", ctypes.c_ulong),
        ("window", ctypes.c_ulong),
    ]


class XWindowAttributes(ctypes.Structure):
    _fields_: ClassVar = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("border_width", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p),
        ("root", ctypes.c_ulong),
        ("class", ctypes.c_int),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int),
        ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long),
        ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("screen", ctypes.c_void_p),
    ]


GdkFilterFunc = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)


class XEmbedTrayHost:
    """Persistent legacy tray manager, modelled after Cairo-Dock's NaTray."""

    def __init__(self, *, icon_size: int, on_changed: Callable[[], None]) -> None:
        self._icon_size = max(16, icon_size)
        self._on_changed = on_changed
        self._xlib = _load_x11()
        self._gdk_lib = _load_gdk()
        self._display: int = 0
        self._screen = 0
        self._root = 0
        self._selection_atom = 0
        self._manager_atom = 0
        self._opcode_atom = 0
        self._message_data_atom = 0
        self._orientation_atom = 0
        self._padding_atom = 0
        self._icon_size_atom = 0
        self._visual_atom = 0
        self._colors_atom = 0
        self._owner_xid = 0
        self._owner_gdk_window_ptr = 0
        self._window: Gtk.Window | None = None
        self._box: Gtk.Box | None = None
        self._sockets: dict[int, Gtk.Socket] = {}
        self._titles: dict[int, str] = {}
        self._filter_ref: Any | None = None
        self._active = False
        self._unavailable_reason = ""

    @property
    def active(self) -> bool:
        return self._active

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    @property
    def visible(self) -> bool:
        return bool(self._window is not None and self._window.get_visible())

    @property
    def icons(self) -> tuple[LegacyTrayIcon, ...]:
        return tuple(
            LegacyTrayIcon(xid=xid, title=self._titles.get(xid) or f"0x{xid:x}")
            for xid in self._sockets
        )

    def start(
        self,
        *,
        force: bool = False,
        anchor_x: int = 0,
        anchor_y: int = 0,
        position: Position | None = None,
    ) -> bool:
        if self._active:
            self.position_near(anchor_x=anchor_x, anchor_y=anchor_y, position=position)
            return True
        if self._xlib is None or self._gdk_lib is None:
            self._unavailable_reason = "X11 libraries unavailable"
            return False

        display = Gdk.Display.get_default()
        if display is None or not isinstance(display, GdkX11.X11Display):
            self._unavailable_reason = "not running on X11"
            return False

        self._display = hash(display.get_xdisplay())
        screen = display.get_default_screen()
        self._screen = int(screen.get_number())
        self._root = int(screen.get_root_window().get_xid())
        self._selection_atom = self._intern_atom(f"_NET_SYSTEM_TRAY_S{self._screen}")
        self._manager_atom = self._intern_atom("MANAGER")
        self._opcode_atom = self._intern_atom("_NET_SYSTEM_TRAY_OPCODE")
        self._message_data_atom = self._intern_atom("_NET_SYSTEM_TRAY_MESSAGE_DATA")
        self._orientation_atom = self._intern_atom("_NET_SYSTEM_TRAY_ORIENTATION")
        self._padding_atom = self._intern_atom("_NET_SYSTEM_TRAY_PADDING")
        self._icon_size_atom = self._intern_atom("_NET_SYSTEM_TRAY_ICON_SIZE")
        self._visual_atom = self._intern_atom("_NET_SYSTEM_TRAY_VISUAL")
        self._colors_atom = self._intern_atom("_NET_SYSTEM_TRAY_COLORS")

        owner = self._xlib.XGetSelectionOwner(self._display, self._selection_atom)
        if owner and not force:
            self._unavailable_reason = (
                f"owned by {_window_name(self._xlib, self._display, owner)}"
            )
            return False

        self._window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._window.set_decorated(False)
        self._window.set_resizable(False)
        self._window.set_skip_taskbar_hint(True)
        self._window.set_skip_pager_hint(True)
        self._window.set_keep_above(True)
        self._window.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._box.set_border_width(0)
        self._window.add(self._box)
        self._window.realize()
        gdk_window = self._window.get_window()
        if gdk_window is None or not isinstance(gdk_window, GdkX11.X11Window):
            self._unavailable_reason = "X11 owner window unavailable"
            self.stop()
            return False

        self._owner_xid = int(gdk_window.get_xid())
        self._owner_gdk_window_ptr = hash(gdk_window)
        self._set_tray_properties()

        self._xlib.XSetSelectionOwner(
            self._display,
            self._selection_atom,
            self._owner_xid,
            CURRENT_TIME,
        )
        if (
            self._xlib.XGetSelectionOwner(self._display, self._selection_atom)
            != self._owner_xid
        ):
            self._unavailable_reason = "failed to own tray selection"
            self.stop()
            return False

        self._install_filter()
        self._broadcast_manager()
        self._active = True
        self._unavailable_reason = ""
        self.position_near(anchor_x=anchor_x, anchor_y=anchor_y, position=position)
        self._window.show_all()
        return True

    def stop(self) -> None:
        if self._display and self._selection_atom and self._owner_xid:
            owner = self._xlib.XGetSelectionOwner(self._display, self._selection_atom)
            if owner == self._owner_xid:
                self._xlib.XSetSelectionOwner(
                    self._display,
                    self._selection_atom,
                    0,
                    CURRENT_TIME,
                )
                self._xlib.XFlush(self._display)
        if self._filter_ref is not None and self._gdk_lib is not None:
            self._gdk_lib.gdk_window_remove_filter(
                ctypes.c_void_p(self._owner_gdk_window_ptr),
                self._filter_ref,
                None,
            )
            self._filter_ref = None
        for socket in tuple(self._sockets.values()):
            socket.destroy()
        self._sockets.clear()
        self._titles.clear()
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._box = None
        self._owner_xid = 0
        self._owner_gdk_window_ptr = 0
        self._active = False

    def position_near(
        self,
        *,
        anchor_x: int = 0,
        anchor_y: int = 0,
        position: Position | None = None,
    ) -> None:
        if self._window is None:
            return
        pref = self._window.get_preferred_size()[1]
        width = max(pref.width, self._icon_size)
        height = max(pref.height, self._icon_size)
        gap = 4
        if position == Position.TOP:
            x = anchor_x - width // 2
            y = anchor_y + gap
        elif position == Position.LEFT:
            x = anchor_x + gap
            y = anchor_y - height // 2
        elif position == Position.RIGHT:
            x = anchor_x - width - gap
            y = anchor_y - height // 2
        else:
            x = anchor_x - width // 2
            y = anchor_y - height - gap
        screen = self._window.get_screen()
        x = max(0, min(x, max(0, screen.get_width() - width)))
        y = max(0, min(y, max(0, screen.get_height() - height)))
        self._window.move(int(x), int(y))

    def show(self) -> None:
        if self._window is not None:
            self._window.show_all()

    def hide(self) -> None:
        if self._window is not None:
            self._window.hide()

    def toggle_visible(
        self,
        *,
        anchor_x: int = 0,
        anchor_y: int = 0,
        position: Position | None = None,
    ) -> bool:
        if not self._active or self._window is None:
            return False
        if self.visible:
            self.hide()
            return False
        self.position_near(anchor_x=anchor_x, anchor_y=anchor_y, position=position)
        self.show()
        return True

    def _install_filter(self) -> None:
        self._gdk_lib.gdk_window_add_filter.argtypes = [
            ctypes.c_void_p,
            GdkFilterFunc,
            ctypes.c_void_p,
        ]
        self._gdk_lib.gdk_window_add_filter.restype = None
        self._gdk_lib.gdk_window_remove_filter.argtypes = [
            ctypes.c_void_p,
            GdkFilterFunc,
            ctypes.c_void_p,
        ]
        self._gdk_lib.gdk_window_remove_filter.restype = None
        self._filter_ref = GdkFilterFunc(self._filter)
        self._gdk_lib.gdk_window_add_filter(
            ctypes.c_void_p(self._owner_gdk_window_ptr),
            self._filter_ref,
            None,
        )

    def _filter(self, xevent_ptr, _gdk_event_ptr, _user_data) -> int:
        try:
            return self._handle_xevent(int(xevent_ptr))
        except Exception as exc:
            log.debug("XEmbed tray event handling failed: %s", exc)
            return GDK_FILTER_CONTINUE

    def _handle_xevent(self, xevent_ptr: int) -> int:
        if not xevent_ptr:
            return GDK_FILTER_CONTINUE
        event_type = ctypes.cast(
            ctypes.c_void_p(xevent_ptr),
            ctypes.POINTER(ctypes.c_int),
        ).contents.value
        if event_type == CLIENT_MESSAGE:
            event = ctypes.cast(
                ctypes.c_void_p(xevent_ptr),
                ctypes.POINTER(XClientMessageEvent),
            ).contents
            if event.message_type == self._opcode_atom:
                opcode = int(event.data.l[1])
                if opcode == SYSTEM_TRAY_REQUEST_DOCK:
                    self._dock_icon(int(event.data.l[2]))
                    return GDK_FILTER_REMOVE
                return GDK_FILTER_REMOVE
            if event.message_type == self._message_data_atom:
                return GDK_FILTER_REMOVE
        elif event_type == DESTROY_NOTIFY:
            event = ctypes.cast(
                ctypes.c_void_p(xevent_ptr),
                ctypes.POINTER(XDestroyWindowEvent),
            ).contents
            self._remove_icon(int(event.window))
            return GDK_FILTER_REMOVE
        elif event_type == SELECTION_CLEAR:
            GLib.idle_add(self._stop_from_selection_clear)
            return GDK_FILTER_REMOVE
        return GDK_FILTER_CONTINUE

    def _stop_from_selection_clear(self) -> bool:
        self.stop()
        self._on_changed()
        return False

    def _dock_icon(self, xid: int) -> None:
        if not xid or xid in self._sockets or self._box is None:
            return
        if not self._foreign_window_exists(xid):
            log.debug("Ignoring vanished XEmbed tray icon xid=0x%x", xid)
            return
        socket = Gtk.Socket()
        socket.set_size_request(self._icon_size, self._icon_size)
        self._box.pack_start(socket, False, False, 0)
        self._box.show_all()
        socket.realize()
        socket.connect("plug-removed", lambda _socket, xid=xid: self._remove_icon(xid))
        x_error = self._trap_x_errors(lambda: socket.add_id(xid))
        if x_error:
            log.debug("Failed to add XEmbed tray icon xid=0x%x error=%s", xid, x_error)
            socket.destroy()
            return
        plug_window = None

        def get_plug_window() -> None:
            nonlocal plug_window
            plug_window = socket.get_plug_window()

        x_error = self._trap_x_errors(get_plug_window)
        if x_error or plug_window is None:
            log.debug(
                "XEmbed tray icon xid=0x%x did not create a plug window (error=%s)",
                xid,
                x_error,
            )
            socket.destroy()
            return
        self._sockets[xid] = socket
        self._titles[xid] = _window_name(self._xlib, self._display, xid)
        self._trap_x_errors(
            lambda: self._xlib.XSelectInput(self._display, xid, STRUCTURE_NOTIFY_MASK),
        )
        self._xlib.XFlush(self._display)
        if self._window is not None:
            self._window.resize(1, 1)
            self._window.show_all()
        self._on_changed()

    def _remove_icon(self, xid: int) -> None:
        socket = self._sockets.pop(xid, None)
        self._titles.pop(xid, None)
        if socket is not None:
            socket.destroy()
            self._on_changed()

    def _broadcast_manager(self) -> None:
        event = XClientMessageEvent()
        event.type = CLIENT_MESSAGE
        event.window = self._root
        event.message_type = self._manager_atom
        event.format = 32
        event.data.l[0] = CURRENT_TIME
        event.data.l[1] = self._selection_atom
        event.data.l[2] = self._owner_xid
        self._xlib.XSendEvent(
            self._display,
            self._root,
            False,
            STRUCTURE_NOTIFY_MASK,
            ctypes.byref(event),
        )
        self._xlib.XFlush(self._display)

    def _set_tray_properties(self) -> None:
        self._change_cardinal_property(self._orientation_atom, 0)
        self._change_cardinal_property(self._padding_atom, 0)
        self._change_cardinal_property(self._icon_size_atom, self._icon_size)
        self._set_visual_property()
        self._set_colors_property()

    def _set_visual_property(self) -> None:
        if self._window is None:
            return
        screen = self._window.get_screen()
        display = self._window.get_display()
        visual = None
        if display.supports_composite():
            visual = screen.get_rgba_visual()
        visual = visual or screen.get_system_visual()
        if visual is None or not isinstance(visual, GdkX11.X11Visual):
            return
        xvisual = visual.get_xvisual()
        visual_id = self._xlib.XVisualIDFromVisual(hash(xvisual))
        visual_id_atom = self._intern_atom("VISUALID")
        data = (ctypes.c_ulong * 1)(int(visual_id))
        self._xlib.XChangeProperty(
            self._display,
            self._owner_xid,
            self._visual_atom,
            visual_id_atom,
            32,
            PROP_MODE_REPLACE,
            ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte)),
            1,
        )

    def _set_colors_property(self) -> None:
        data = (ctypes.c_ulong * 12)(
            0,
            0,
            0,
            0xFFFF,
            0,
            0,
            0xFFFF,
            0xFFFF,
            0,
            0,
            0xFFFF,
            0,
        )
        cardinal = self._intern_atom("CARDINAL")
        self._xlib.XChangeProperty(
            self._display,
            self._owner_xid,
            self._colors_atom,
            cardinal,
            32,
            PROP_MODE_REPLACE,
            ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte)),
            12,
        )

    def _change_cardinal_property(self, atom: int, value: int) -> None:
        cardinal = self._intern_atom("CARDINAL")
        data = (ctypes.c_ulong * 1)(int(value))
        self._xlib.XChangeProperty(
            self._display,
            self._owner_xid,
            atom,
            cardinal,
            32,
            PROP_MODE_REPLACE,
            ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte)),
            1,
        )

    def _intern_atom(self, name: str) -> int:
        return int(self._xlib.XInternAtom(self._display, name.encode(), False))

    def _foreign_window_exists(self, xid: int) -> bool:
        attributes = XWindowAttributes()
        result = 0

        def check() -> None:
            nonlocal result
            result = self._xlib.XGetWindowAttributes(
                self._display,
                xid,
                ctypes.byref(attributes),
            )

        x_error = self._trap_x_errors(check)
        return not x_error and bool(result)

    def _trap_x_errors(self, fn: Callable[[], object]) -> int:
        display = Gdk.Display.get_default()
        if display is None or not isinstance(display, GdkX11.X11Display):
            fn()
            return 0
        display.error_trap_push()
        try:
            fn()
        except Exception:
            display.error_trap_pop_ignored()
            raise
        return int(display.error_trap_pop())


def _load_x11():
    path = ctypes.util.find_library("X11")
    if not path:
        return None
    lib = ctypes.CDLL(path)
    lib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    lib.XGetSelectionOwner.restype = ctypes.c_ulong
    lib.XSetSelectionOwner.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.XInternAtom.restype = ctypes.c_ulong
    lib.XSendEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_long,
        ctypes.c_void_p,
    ]
    lib.XSendEvent.restype = ctypes.c_int
    lib.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
    lib.XGetWindowAttributes.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(XWindowAttributes),
    ]
    lib.XGetWindowAttributes.restype = ctypes.c_int
    lib.XVisualIDFromVisual.argtypes = [ctypes.c_void_p]
    lib.XVisualIDFromVisual.restype = ctypes.c_ulong
    lib.XChangeProperty.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int,
    ]
    lib.XFlush.argtypes = [ctypes.c_void_p]
    lib.XFetchName.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_char_p),
    ]
    lib.XFetchName.restype = ctypes.c_int
    lib.XFree.argtypes = [ctypes.c_void_p]
    return lib


def _load_gdk():
    path = ctypes.util.find_library("gdk-3")
    return ctypes.CDLL(path) if path else None


def _window_name(xlib, display: int, xid: int) -> str:
    gdk_display = Gdk.Display.get_default()
    if isinstance(gdk_display, GdkX11.X11Display):
        gdk_display.error_trap_push()
    name = ctypes.c_char_p()
    result = xlib.XFetchName(display, xid, ctypes.byref(name))
    if isinstance(gdk_display, GdkX11.X11Display):
        x_error = gdk_display.error_trap_pop()
        if x_error:
            return f"0x{xid:x}"
    if result and name.value:
        value = name.value.decode(errors="replace")
        xlib.XFree(name)
        return value
    return f"0x{xid:x}"
