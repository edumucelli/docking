"""GTK source lifecycle around a native, memory-only Shell COPY drag."""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from typing import cast
from weakref import WeakKeyDictionary

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from docking.log import get_logger
from docking.platform.applications.drag import (
    APPLICATION_DRAG_INFO,
    APPLICATION_DRAG_MIME,
)

from .com import HRESULT
from .dataobject import cached_data_object
from .ffi import bind, window_handle

log = get_logger("windows.application_drag")
Payloads = tuple[tuple[str, bytes], ...]


@dataclass
class _Handoff:
    context: Gdk.DragContext
    data: Payloads
    finished_handler: int = 0
    gtk_ended: bool = False
    native_finished: bool = False


@contextmanager
def _gtk_events(user: object, on_error: Callable[[], None]) -> Iterator[int]:
    """Dispatch GLib while the Shell owns its modal drag loop."""
    context = GLib.MainContext.default()
    callback_type = ctypes.WINFUNCTYPE(
        None, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_uint32
    )

    def pulse(_window, _message, _timer, _time):
        # Exceptions cannot propagate through this native timer callback.
        try:
            for _ in range(32):
                if not context.pending():
                    break
                context.iteration(False)
        except Exception:
            log.exception("Could not dispatch GTK during a native file drag")
            on_error()

    timer_callback = callback_type(pulse)

    hwnd = bind(
        user,
        "CreateWindowExW",
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )(0, "STATIC", "", 0, 0, 0, 0, 0, -3, None, None, None)  # HWND_MESSAGE
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    timer = 0
    modal = None
    try:
        # GTK 3 exports this compatibility hook for its native dialogs. Without
        # it, GDK drains the Win32 queue during iteration and steals OLE's
        # Escape/button-up messages. Our owned window receives its wakeups.
        set_modal = bind(
            ctypes.CDLL("libgdk-3-0.dll"),
            "gdk_win32_set_modal_dialog_libgtk_only",
            None,
            ctypes.c_void_p,
        )
        timer = bind(
            user,
            "SetTimer",
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            callback_type,
        )(hwnd, 1, 16, timer_callback)
        if not timer:
            raise ctypes.WinError(ctypes.get_last_error())
        set_modal(hwnd)
        modal = set_modal
        yield hwnd
    finally:
        if modal is not None:
            modal(None)
        if timer:
            bind(user, "KillTimer", ctypes.c_int32, ctypes.c_void_p, ctypes.c_size_t)(
                hwnd, 1
            )
            # KillTimer leaves queued messages behind. Drain only this owned
            # window before releasing the Python TIMERPROC callback.
            message = wintypes.MSG()
            peek = bind(
                user,
                "PeekMessageW",
                ctypes.c_int32,
                ctypes.POINTER(wintypes.MSG),
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_uint32,
            )
            while peek(ctypes.byref(message), hwnd, 0x113, 0x113, 1):
                pass
        bind(user, "DestroyWindow", ctypes.c_int32, ctypes.c_void_p)(hwnd)


class WindowsFileDragSource:
    """Own pending GTK handoffs, native COPY operations and shutdown cleanup."""

    def __init__(self, host: object) -> None:
        self._host = cast(Gtk.Widget, host)
        self._widgets: WeakKeyDictionary[Gtk.Widget, tuple[int, ...]] = (
            WeakKeyDictionary()
        )
        self._sources: set[int] = set()
        self._pending: _Handoff | None = None
        self._stopped = False
        self._active = False
        self._native_window = 0
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._host_handler = self._host.connect("destroy", lambda *_args: self.stop())

    def prepare(self, widget: object, *, application_id: str, data: Payloads) -> None:
        source = cast(Gtk.Widget, widget)
        source.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            [
                Gtk.TargetEntry.new(
                    APPLICATION_DRAG_MIME,
                    Gtk.TargetFlags.SAME_APP,
                    APPLICATION_DRAG_INFO,
                )
            ],
            Gdk.DragAction.COPY,
        )
        payloads = (*data, (APPLICATION_DRAG_MIME, application_id.encode("utf-8")))
        self._widgets[source] = (
            source.connect("drag-begin", self._begin, payloads),
            source.connect("drag-end", self._ended),
            source.connect("drag-failed", lambda *_args: True),
            source.connect("drag-data-get", self._data_get, application_id),
        )

    def _later(self, callback: Callable[[], object]) -> None:
        token = [0]

        def invoke() -> bool:
            self._sources.discard(token[0])
            if not self._stopped:
                callback()
            return False

        token[0] = GLib.idle_add(invoke)
        self._sources.add(token[0])

    def _begin(
        self, _widget: Gtk.Widget, context: Gdk.DragContext, data: Payloads
    ) -> None:
        pending = _Handoff(context, data)
        self._pending = pending
        pending.finished_handler = context.connect("dnd-finished", self._finished)
        # GTK has not finished constructing its source info inside drag-begin.
        # Cancel on the next dispatch and start native OLE after drag-end.
        self._later(lambda: self._cancel(context))

    def _cancel(self, context: Gdk.DragContext) -> None:
        pending = self._pending
        if pending is not None and pending.context == context and not pending.gtk_ended:
            Gtk.drag_cancel(context)

    def _ended(self, _widget: Gtk.Widget, context: Gdk.DragContext) -> None:
        pending = self._pending
        if pending is not None and context == pending.context:
            pending.gtk_ended = True
            self._continue_handoff()

    def _finished(self, context: Gdk.DragContext) -> None:
        pending = self._pending
        if pending is not None and context == pending.context:
            pending.native_finished = True
            self._continue_handoff()

    def _continue_handoff(self) -> None:
        pending = self._pending
        if pending is None or not pending.gtk_ended or not pending.native_finished:
            return
        self._pending = None
        pending.context.disconnect(pending.finished_handler)
        # Win32 emits dnd-finished after its original DoDragDrop returns. GTK's
        # drag-end alone can occur while that native loop is still unwinding.
        self._later(lambda: self._run(pending.data))

    @staticmethod
    def _data_get(
        _widget, _context, selection, _info, _time, application_id: str
    ) -> None:
        selection.set(selection.get_target(), 8, application_id.encode("utf-8"))

    def _run(self, data: Payloads) -> None:
        if self._stopped or self._active:
            return
        if (
            not bind(self._user, "GetAsyncKeyState", ctypes.c_int16, ctypes.c_int32)(1)
            & 0x8000
        ):
            return  # The user may already have released a very short gesture.
        window = self._host.get_window()
        if window is None:
            return
        self._active = True
        try:
            with (
                cached_data_object(data) as pointer,
                _gtk_events(self._user, self.stop) as native_window,
            ):
                self._native_window = native_window
                try:
                    effect = ctypes.c_uint32()
                    status = bind(
                        ctypes.WinDLL("shell32", use_last_error=True),
                        "SHDoDragDrop",
                        HRESULT,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                        ctypes.c_uint32,
                        ctypes.POINTER(ctypes.c_uint32),
                    )(window_handle(window), pointer, None, 1, ctypes.byref(effect))
                    if status < 0:
                        raise OSError(status & 0xFFFFFFFF, "Native file drag failed")
                finally:
                    self._native_window = 0
        except OSError:
            log.exception("Could not export an application shortcut")
        finally:
            self._active = False

    def stop(self) -> None:
        self._stopped = True
        for source in self._sources:
            GLib.source_remove(source)
        self._sources.clear()
        pending, self._pending = self._pending, None
        if pending is not None:
            pending.context.disconnect(pending.finished_handler)
            if not pending.gtk_ended:
                Gtk.drag_cancel(pending.context)
        if self._active:
            if self._native_window:
                # OLE's default source does not cancel when its owner is
                # destroyed. Send Escape only to our own modal-loop window.
                posted = bind(
                    self._user,
                    "PostMessageW",
                    ctypes.c_int32,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.c_size_t,
                    ctypes.c_ssize_t,
                )(self._native_window, 0x100, 0x1B, 0)
                if not posted:
                    log.error(
                        "Could not cancel native application drag: %s",
                        ctypes.WinError(ctypes.get_last_error()),
                    )
            bind(self._user, "ReleaseCapture", ctypes.c_int32)()
        for widget, handlers in tuple(self._widgets.items()):
            for handler in handlers:
                if widget.handler_is_connected(handler):
                    widget.disconnect(handler)
        self._widgets.clear()
        if self._host_handler:
            if self._host.handler_is_connected(self._host_handler):
                self._host.disconnect(self._host_handler)
            self._host_handler = 0
