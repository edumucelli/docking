"""Scoped keyboard focus and close routing for GTK menus on Windows."""

from __future__ import annotations

import ctypes
from collections import deque
from ctypes import wintypes
from weakref import WeakSet

from gi.repository import GLib, Gtk

from docking.log import get_logger

from .close_hook import WindowsCloseHook
from .ffi import bind as _bind

log = get_logger("windows.menus")


class WindowsMenuFocus:
    """Observe foreground ownership without changing other application settings."""

    def __init__(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        self._foreground = _bind(user, "GetForegroundWindow", wintypes.HWND)
        self._active = _bind(user, "GetActiveWindow", wintypes.HWND)
        self._set_foreground = _bind(
            user, "SetForegroundWindow", wintypes.BOOL, wintypes.HWND
        )
        self._pid = _bind(
            user,
            "GetWindowThreadProcessId",
            wintypes.DWORD,
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        callback_type = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HWND,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self._event_callback = callback_type(self._event)
        self._hook = _bind(
            user,
            "SetWinEventHook",
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            callback_type,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self._unhook = _bind(user, "UnhookWinEvent", wintypes.BOOL, wintypes.HANDLE)
        self._event_hook = None
        self._changed = None

    def start(self, changed):
        if self._event_hook:
            return
        self._changed = changed
        # Out-of-context foreground events arrive on the installing GTK thread.
        # A non-activating right click can temporarily clear GetForegroundWindow
        # before GTK delivers the button event; retain the preceding identity.
        self._event_hook = self._hook(3, 3, None, self._event_callback, 0, 0, 0)
        if not self._event_hook:
            self._changed = None
            raise ctypes.WinError(ctypes.get_last_error())
        changed(self.foreground())

    def _event(self, _hook, _event, hwnd, _object, _child, _thread, _time):
        try:
            if self._changed is not None:
                self._changed(self.identity(hwnd))
        except Exception:
            log.exception("Native foreground observer failed")

    def stop(self):
        self._changed = None
        if self._event_hook:
            self._unhook(self._event_hook)
            self._event_hook = None

    def identity(self, hwnd):
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        if not self._pid(hwnd, ctypes.byref(pid)) or not pid.value:
            return None
        return int(hwnd), pid.value

    def foreground(self):
        return self.identity(self._foreground())

    def active(self):
        return self.identity(self._active())

    def restore(self, previous, parent):
        if (
            previous
            and parent
            and previous != parent
            and self.identity(previous[0]) == previous
        ):
            current = self.foreground()
            if current is None:
                return False
            if current != parent:
                return True
            self._set_foreground(previous[0])
            # Cross-queue activation can still be pending while a GTK menu is
            # releasing its native grab. The caller may retry only while the
            # dock continues to own foreground and the original HWND/PID lives.
            current = self.foreground()
            return bool(current) and current != parent
        return True


class WindowsMenuSession:
    """Temporarily focus a dock for its menus, keeping hover non-activating.

    GDK Win32 records keyboard grabs but does not move native focus for them.
    GTK also suppresses a parent's delete event while another toplevel owns a
    menu grab. A close hook on the dock's HWND queues dismissal before requesting
    the normal GTK close. Native callbacks never enter GTK or replace its event
    dispatcher.
    """

    def __init__(self, parent, *, native=None, hook_factory=WindowsCloseHook):
        self._parent = parent
        self._native = native if native is not None else WindowsMenuFocus()
        self._close_hook = hook_factory(self._request_close)
        self._close_source = 0
        self._watched = WeakSet()
        self._capture_surfaces = WeakSet()
        self._capture_session = False
        self._active = set()
        self._in_session = False
        self._saved_accept_focus = False
        self._previous = None
        self._parent_identity = None
        self._last_foreground = None
        self._foreground_history = deque(maxlen=8)
        self._finish_source = 0
        self._restore_attempts = 0
        self._stopped = False
        self._native.start(self._observe_foreground)

    def _observe_foreground(self, identity):
        if not self._stopped and identity and identity != self._parent_identity:
            if (
                self._capture_session
                and self._parent_identity
                and identity[1] == self._parent_identity[1]
            ):
                # A capture popup briefly owns foreground itself. Its handle
                # must not replace the source app for the next pick.
                return
            self._last_foreground = identity
            if identity in self._foreground_history:
                self._foreground_history.remove(identity)
            self._foreground_history.append(identity)

    def _remembered_foreground(self):
        # Closing a transient can restore its preceding foreground window
        # without delivering another foreground event. Skip dead preview/dialog
        # handles while retaining HWND/PID validation for every candidate.
        for identity in reversed(self._foreground_history):
            if (
                identity != self._parent_identity
                and self._native.identity(identity[0]) == identity
            ):
                return identity
        return None

    def prepare_capture(self, overlay):
        self._capture_session = True
        self._capture_surfaces.add(overlay)
        self.prepare(overlay, capture=True)

    def bind_parent(self, native_window: object | None) -> None:
        """Identify the dock before its first click can change foreground."""
        if native_window is None:
            return
        from .ffi import window_handle

        hwnd = window_handle(native_window)
        if hwnd:
            self._parent_identity = self._native.identity(hwnd)
            if self._parent_identity is not None:
                self._close_hook.install(hwnd)

    def prepare(self, menu, *, capture=False):
        if self._stopped:
            return
        if self._finish_source:
            GLib.source_remove(self._finish_source)
            self._finish_source = 0
            if self._in_session and not self._active:
                foreground = self._native.foreground() or self._remembered_foreground()
                if foreground and foreground not in (
                    self._previous,
                    self._parent_identity,
                ):
                    self._in_session = False
        if not self._in_session:
            self._in_session = True
            self._restore_attempts = 0
            self._saved_accept_focus = self._parent.get_accept_focus()
            foreground = self._native.foreground()
            self._previous = (
                foreground
                if foreground and foreground != self._parent_identity
                else self._remembered_foreground()
            )
        if menu not in self._watched:
            self._watched.add(menu)
            menu.connect("hide", self._closed)
            menu.connect("destroy" if capture else "deactivate", self._closed)
        self._active.add(menu)
        self._parent.set_accept_focus(True)
        self._parent.present_with_time(Gtk.get_current_event_time())
        self._parent_identity = self._native.active()
        if self._parent_identity:
            self._close_hook.install(self._parent_identity[0])

    def _closed(self, menu):
        if menu not in self._active:
            return
        self._active.discard(menu)
        if not self._active:
            self._parent.set_accept_focus(self._saved_accept_focus)
            if not self._finish_source and not self._stopped:
                # Restore after item activation has had a chance to present its
                # own application, Preferences or Search window.
                self._finish_source = GLib.idle_add(self._finish)

    def _finish(self):
        self._finish_source = 0
        if not self._in_session or self._active:
            return False
        self._restore_attempts += 1
        restored = self._native.restore(self._previous, self._parent_identity)
        if restored is False and not self._stopped and self._restore_attempts < 5:
            self._finish_source = GLib.timeout_add(50, self._finish)
            return False
        if self._capture_session:
            self._capture_session = False
            # Search or another app may have taken focus during capture. Keep
            # that current identity after the normal restoration ownership check.
            self._observe_foreground(self._native.foreground())
        self._in_session = False
        self._previous = None
        return False

    def _request_close(self):
        if self._stopped or not self._active:
            return False
        if not self._close_source:
            self._close_source = GLib.idle_add(self._close)
        return True

    def capture_source(self) -> tuple[int, int] | None:
        if self._stopped:
            return None
        source = self._previous if self._in_session else self._native.foreground()
        if source is None or source == self._parent_identity:
            source = self._remembered_foreground()
        if (
            source
            and source != self._parent_identity
            and self._native.identity(source[0]) == source
        ):
            return int(source[0]), int(source[1])
        return None

    def _close(self):
        self._close_source = 0
        self.dismiss()
        self._parent.close()
        return False

    def dismiss(self):
        for menu in tuple(self._active):
            if menu in self._capture_surfaces:
                menu.destroy()
            else:
                menu.popdown()
            self._closed(menu)

    def stop(self):
        self._stopped = True
        self._native.stop()
        if self._close_source:
            GLib.source_remove(self._close_source)
            self._close_source = 0
        self._close_hook.close()
        self.dismiss()
        if self._finish_source:
            GLib.source_remove(self._finish_source)
            self._finish_source = 0
        self._finish()
        self._watched.clear()
        self._capture_surfaces.clear()
