"""Dependency-free X11 global shortcuts isolated in a helper process."""

from __future__ import annotations

import ctypes
import ctypes.util
import multiprocessing
import os
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, ClassVar, Protocol

KEY_PRESS = 2
GRAB_MODE_ASYNC = 1
SHIFT_MASK = 1 << 0
LOCK_MASK = 1 << 1
CONTROL_MASK = 1 << 2
MOD1_MASK = 1 << 3
MOD2_MASK = 1 << 4
MOD4_MASK = 1 << 6

ActivationCallback = Callable[[int], None]
IdleScheduler = Callable[..., int]


class ShortcutFallback(Protocol):
    @property
    def active(self) -> bool: ...

    @property
    def error(self) -> str | None: ...

    def start(self) -> bool: ...

    def stop(self) -> None: ...


class ShortcutWorker(Protocol):
    @property
    def error(self) -> str | None: ...

    def start(self, on_activated: ActivationCallback) -> bool: ...

    def stop(self) -> None: ...


class _XKeyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("x_root", ctypes.c_int),
        ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("keycode", ctypes.c_uint),
        ("same_screen", ctypes.c_int),
    ]


class _XEvent(ctypes.Union):
    _fields_: ClassVar[list[tuple[str, object]]] = [
        ("type", ctypes.c_int),
        ("xkey", _XKeyEvent),
        ("pad", ctypes.c_long * 24),
    ]


class _XErrorEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resource_id", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


_XErrorHandler = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.POINTER(_XErrorEvent),
)


def parse_xdg_shortcut(shortcut: str) -> tuple[int, str]:
    parts = [part.strip() for part in shortcut.split("+") if part.strip()]
    if not parts:
        raise ValueError("shortcut must contain a key")
    modifiers = 0
    modifier_bits = {
        "SHIFT": SHIFT_MASK,
        "CTRL": CONTROL_MASK,
        "ALT": MOD1_MASK,
        "NUM": MOD2_MASK,
        "LOGO": MOD4_MASK,
    }
    for modifier in parts[:-1]:
        try:
            modifiers |= modifier_bits[modifier.upper()]
        except KeyError as exc:
            raise ValueError(f"unknown shortcut modifier: {modifier}") from exc
    key_name = parts[-1]
    if key_name.upper() in modifier_bits:
        raise ValueError("shortcut must contain a non-modifier key")
    if not all(character.isalnum() or character == "_" for character in key_name):
        raise ValueError(f"invalid shortcut key: {key_name}")
    return modifiers, key_name


def is_x11_session(env: dict[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    session_type = values.get("XDG_SESSION_TYPE", "").strip().casefold()
    if session_type:
        return session_type == "x11"
    return bool(values.get("DISPLAY")) and not bool(values.get("WAYLAND_DISPLAY"))


class _ProcessShortcutWorker:
    def __init__(
        self,
        *,
        shortcut: str,
        display_name: str | None,
        library_path: str | None,
    ) -> None:
        self._shortcut = shortcut
        self._display_name = display_name
        self._library_path = library_path
        self._process: Any = None
        self._stop_event: Any = None
        self._connection: Any = None
        self._monitor: threading.Thread | None = None
        self._startup = threading.Event()
        self._active = False
        self._error: str | None = None

    @property
    def error(self) -> str | None:
        return self._error

    def start(self, on_activated: ActivationCallback) -> bool:
        try:
            context = multiprocessing.get_context("forkserver")
        except ValueError:
            context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        stop_event = context.Event()
        process = context.Process(
            target=_x11_worker,
            args=(
                self._shortcut,
                self._display_name,
                self._library_path,
                stop_event,
                sender,
            ),
            name="docking-x11-shortcut",
            daemon=True,
        )
        self._startup.clear()
        self._error = None
        self._connection = receiver
        self._stop_event = stop_event
        self._process = process
        process.start()
        sender.close()
        self._monitor = threading.Thread(
            target=self._monitor_worker,
            args=(on_activated,),
            name="docking-x11-shortcut-monitor",
            daemon=True,
        )
        self._monitor.start()
        self._startup.wait(timeout=2.0)
        if not self._active:
            self.stop()
        return self._active

    def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        process = self._process
        if process is not None:
            process.join(timeout=1.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
        connection = self._connection
        if connection is not None:
            with suppress(OSError):
                connection.close()
        monitor = self._monitor
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=1.0)
        self._process = None
        self._stop_event = None
        self._connection = None
        self._monitor = None
        self._active = False

    def _monitor_worker(self, on_activated: ActivationCallback) -> None:
        connection = self._connection
        process = self._process
        if connection is None or process is None:
            self._startup.set()
            return
        while process.is_alive() or connection.poll():
            try:
                if not connection.poll(0.1):
                    continue
                message, value = connection.recv()
            except (EOFError, OSError):
                break
            if message == "ready":
                self._active = True
                self._startup.set()
            elif message == "error":
                self._error = str(value)
                self._startup.set()
            elif message == "activate" and self._active:
                on_activated(int(value))
        self._startup.set()


class X11GlobalShortcutService:
    """Own one per-generation X11 shortcut helper process."""

    def __init__(
        self,
        *,
        shortcut: str,
        on_activated: ActivationCallback,
        schedule_idle: IdleScheduler,
        display_name: str | None = None,
        library_path: str | None = None,
        worker_factory: Callable[[], ShortcutWorker] | None = None,
    ) -> None:
        self._shortcut = shortcut
        self._on_activated = on_activated
        self._schedule_idle = schedule_idle
        self._display_name = display_name
        self._library_path = library_path
        self._worker_factory = worker_factory
        self._worker: ShortcutWorker | None = None
        self._generation = 0
        self._active = False
        self._error: str | None = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def error(self) -> str | None:
        return self._error

    def start(self) -> bool:
        if self._active:
            return True
        self._generation += 1
        generation = self._generation
        worker = (
            self._worker_factory()
            if self._worker_factory is not None
            else _ProcessShortcutWorker(
                shortcut=self._shortcut,
                display_name=self._display_name,
                library_path=self._library_path,
            )
        )
        self._worker = worker
        self._error = None
        self._active = worker.start(
            lambda timestamp: self._queue_activation(timestamp, generation)
        )
        if not self._active:
            self._error = worker.error
            self._worker = None
        return self._active

    def stop(self) -> None:
        self._generation += 1
        worker = self._worker
        self._worker = None
        self._active = False
        self._error = None
        if worker is not None:
            worker.stop()

    def _queue_activation(self, timestamp: int, generation: int) -> None:
        self._schedule_idle(self._dispatch_activation, timestamp, generation)

    def _dispatch_activation(self, timestamp: int, generation: int) -> bool:
        if self._active and generation == self._generation:
            self._on_activated(timestamp)
        return False


def _x11_worker(
    shortcut: str,
    display_name: str | None,
    library_path: str | None,
    stop_event: Any,
    connection: Any,
) -> None:
    library = None
    display = None
    keycode = 0
    root = 0
    grabbed: tuple[int, ...] = ()
    try:
        modifiers, key_name = parse_xdg_shortcut(shortcut)
        library = _load_library(library_path)
        display_value = display_name.encode() if display_name is not None else None
        display = library.XOpenDisplay(display_value)
        if not display:
            raise RuntimeError("could not open the X11 display")
        root = int(library.XDefaultRootWindow(display))
        keysym = int(library.XStringToKeysym(key_name.encode()))
        if keysym == 0:
            raise ValueError(f"unknown X11 key: {key_name}")
        keycode = int(library.XKeysymToKeycode(display, keysym))
        if keycode == 0:
            raise ValueError(f"X11 has no keycode for: {key_name}")
        grabbed = tuple(
            sorted(
                {
                    modifiers | ignored
                    for ignored in (0, LOCK_MASK, MOD2_MASK, LOCK_MASK | MOD2_MASK)
                }
            )
        )
        _grab_keys(
            library=library,
            display=display,
            keycode=keycode,
            modifiers=grabbed,
            root=root,
        )
        connection.send(("ready", None))
        event = _XEvent()
        last_activation = 0.0
        while not stop_event.wait(0.03):
            while library.XPending(display) > 0:
                library.XNextEvent(display, ctypes.byref(event))
                if event.type != KEY_PRESS:
                    continue
                now = time.monotonic()
                if now - last_activation < 0.2:
                    continue
                last_activation = now
                connection.send(("activate", int(event.xkey.time)))
    except Exception as exc:
        with suppress(BrokenPipeError, EOFError, OSError):
            connection.send(("error", str(exc) or type(exc).__name__))
    finally:
        if library is not None and display:
            for grab_modifiers in grabbed:
                library.XUngrabKey(display, keycode, grab_modifiers, root)
            library.XSync(display, False)
            library.XCloseDisplay(display)
        connection.close()


def _grab_keys(
    *,
    library: Any,
    display: Any,
    keycode: int,
    modifiers: tuple[int, ...],
    root: int,
) -> None:
    errors: list[int] = []

    @_XErrorHandler
    def capture_error(_display: ctypes.c_void_p, event: Any) -> int:
        errors.append(int(event.contents.error_code))
        return 0

    previous_handler = library.XSetErrorHandler(
        ctypes.cast(capture_error, ctypes.c_void_p)
    )
    try:
        for grab_modifiers in modifiers:
            library.XGrabKey(
                display,
                keycode,
                grab_modifiers,
                root,
                False,
                GRAB_MODE_ASYNC,
                GRAB_MODE_ASYNC,
            )
        library.XSync(display, False)
    finally:
        library.XSetErrorHandler(previous_handler)
    if errors:
        for grab_modifiers in modifiers:
            library.XUngrabKey(display, keycode, grab_modifiers, root)
        library.XSync(display, False)
        raise RuntimeError("shortcut is already in use")


def _load_library(library_path: str | None = None) -> Any:
    path = library_path or ctypes.util.find_library("X11")
    if not path:
        raise RuntimeError("libX11 is unavailable")
    library = ctypes.CDLL(path)
    library.XOpenDisplay.argtypes = [ctypes.c_char_p]
    library.XOpenDisplay.restype = ctypes.c_void_p
    library.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    library.XDefaultRootWindow.restype = ctypes.c_ulong
    library.XStringToKeysym.argtypes = [ctypes.c_char_p]
    library.XStringToKeysym.restype = ctypes.c_ulong
    library.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    library.XKeysymToKeycode.restype = ctypes.c_uint
    library.XGrabKey.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    library.XUngrabKey.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_ulong,
    ]
    library.XPending.argtypes = [ctypes.c_void_p]
    library.XPending.restype = ctypes.c_int
    library.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XEvent)]
    library.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    library.XSetErrorHandler.restype = ctypes.c_void_p
    library.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.XCloseDisplay.argtypes = [ctypes.c_void_p]
    return library


__all__ = [
    "CONTROL_MASK",
    "LOCK_MASK",
    "MOD1_MASK",
    "MOD2_MASK",
    "MOD4_MASK",
    "SHIFT_MASK",
    "ShortcutFallback",
    "ShortcutWorker",
    "X11GlobalShortcutService",
    "is_x11_session",
    "parse_xdg_shortcut",
]
