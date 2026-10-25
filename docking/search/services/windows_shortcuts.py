"""Native Windows global Search shortcut with main-loop lifecycle callbacks."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, RLock, Thread
from typing import Protocol

from docking.log import get_logger
from docking.platform.backends.windows.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    WindowsHotkeyAPI,
)
from docking.search.services.global_shortcuts import (
    TOGGLE_SEARCH_SHORTCUT_ID,
    ActivationCallback,
    GlobalShortcutActivation,
    GlobalShortcutBinding,
    GlobalShortcutsState,
    GlobalShortcutsStatus,
    StatusCallback,
)

log = get_logger("search.windows_shortcuts")
_MODIFIERS = {"CTRL": MOD_CONTROL, "ALT": MOD_ALT, "SHIFT": MOD_SHIFT, "LOGO": MOD_WIN}
_KEYS = {
    "space": 0x20,
    "tab": 0x09,
    "return": 0x0D,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "page_up": 0x21,
    "page_down": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "print": 0x2C,
    "pause": 0x13,
    "menu": 0x5D,
    "kp_enter": 0x0D,
    "kp_add": 0x6B,
    "kp_subtract": 0x6D,
    "kp_multiply": 0x6A,
    "kp_divide": 0x6F,
    "kp_decimal": 0x6E,
    **{f"f{number}": 0x6F + number for number in range(1, 25)},
    **{f"kp_{number}": 0x60 + number for number in range(10)},
}


def parse_windows_shortcut(shortcut: str) -> tuple[int, int | str]:
    """Convert captured GDK names to Win32 modifiers and a key/character."""
    parts = [part.strip() for part in shortcut.split("+")]
    if not parts or not all(parts):
        raise ValueError("shortcut must contain a key")
    modifiers = 0
    for name in parts[:-1]:
        if name.upper() not in _MODIFIERS:
            raise ValueError(f"unsupported Windows shortcut modifier: {name}")
        modifiers |= _MODIFIERS[name.upper()]
    name = parts[-1]
    if name.upper() in _MODIFIERS or name.casefold() == "f12":
        raise ValueError("shortcut needs a usable non-reserved key")
    key: int | str | None = _KEYS.get(name.casefold())
    if key is None:
        # These static key-symbol functions do not access a display or widgets.
        import gi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk

        keyval = Gdk.keyval_from_name(name)
        character = Gdk.keyval_to_unicode(Gdk.keyval_to_lower(keyval))
        if not character or character > 0xFFFF:
            raise ValueError(f"unsupported Windows shortcut key: {name}")
        key = chr(character)
    if not modifiers & (MOD_CONTROL | MOD_ALT | MOD_WIN) and (
        isinstance(key, str) or key in {0x20, 0x0D, 0x09, 0x08, 0x2E}
    ):
        raise ValueError("typing shortcuts need Ctrl, Alt or Logo")
    return modifiers, key


class _NativeHotkey(Protocol):
    def register(self, modifiers: int, key: int | str) -> None: ...
    def activation(self) -> int | None: ...
    def unregister(self) -> None: ...


class WindowsGlobalShortcutsService:
    """Own a thread registration, publishing only current-generation outcomes."""

    def __init__(
        self,
        *,
        preferred_trigger: str,
        trigger_description: str,
        on_activated: ActivationCallback,
        on_status_changed: StatusCallback,
        schedule_idle: Callable[..., int],
        remove_source: Callable[[int], bool],
        native_factory: Callable[[], _NativeHotkey] = WindowsHotkeyAPI,
    ) -> None:
        self._preferred_trigger = preferred_trigger
        self._trigger_description = trigger_description
        self._on_activated = on_activated
        self._on_status_changed = on_status_changed
        self._schedule_idle = schedule_idle
        self._remove_source = remove_source
        self._native_factory = native_factory
        self._status = GlobalShortcutsStatus(GlobalShortcutsState.STOPPED)
        self._generation = 0
        self._started = False
        self._closed = Event()
        self._thread: Thread | None = None
        self._lock = RLock()
        self._sources: set[int] = set()
        self._activation_pending = False

    @property
    def status(self) -> GlobalShortcutsStatus:
        return self._status

    def start(self) -> None:
        """Begin registration without blocking GTK on native setup."""
        if self._started or (self._thread is not None and self._thread.is_alive()):
            return
        self._started = True
        self._generation += 1
        self._closed = Event()
        self._publish(GlobalShortcutsState.STARTING)
        try:
            modifiers, key = parse_windows_shortcut(self._preferred_trigger)
        except ValueError as exc:
            self._publish(GlobalShortcutsState.ERROR, str(exc))
            return
        self._thread = Thread(
            target=self._worker,
            args=(self._generation, self._closed, modifiers, key),
            name="windows-search-hotkey",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Invalidate pending callbacks and release the worker's registration."""
        self._started = False
        with self._lock:
            self._generation += 1
            self._closed.set()
            for source in self._sources:
                self._remove_source(source)
            self._sources.clear()
            self._activation_pending = False
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        self._publish(GlobalShortcutsState.STOPPED)

    def _publish(self, state: GlobalShortcutsState, message: str | None = None) -> None:
        binding = (
            GlobalShortcutBinding(
                TOGGLE_SEARCH_SHORTCUT_ID,
                "Toggle Docking search",
                self._trigger_description,
            )
            if state is GlobalShortcutsState.BOUND
            else None
        )
        status = GlobalShortcutsStatus(state, binding=binding, message=message)
        if status != self._status:
            self._status = status
            self._on_status_changed(status)

    def _post(self, generation: int, callback, *args, activation=False) -> None:
        with self._lock:
            if generation != self._generation or self._closed.is_set():
                return
            if activation and self._activation_pending:
                return
            if activation:
                self._activation_pending = True
            source = 0

            def dispatch():
                with self._lock:
                    self._sources.discard(source)
                    if generation != self._generation or self._closed.is_set():
                        return False
                    if activation:
                        self._activation_pending = False
                callback(*args)
                return False

            source = self._schedule_idle(dispatch)
            self._sources.add(source)

    def _worker(
        self, generation: int, closed: Event, modifiers: int, key: int | str
    ) -> None:
        native = None
        registered = False
        try:
            native = self._native_factory()
            if closed.is_set():
                return
            native.register(modifiers, key)
            registered = True
            self._post(generation, self._publish, GlobalShortcutsState.BOUND)
            while not closed.is_set():
                timestamp = native.activation()
                if timestamp is not None:
                    self._post(
                        generation,
                        self._on_activated,
                        GlobalShortcutActivation(
                            TOGGLE_SEARCH_SHORTCUT_ID, timestamp, None
                        ),
                        activation=True,
                    )
                else:
                    closed.wait(0.02)
        except (OSError, RuntimeError, ValueError) as exc:
            message = (
                "shortcut is already in use"
                if getattr(exc, "winerror", None) == 1409
                else str(exc)
            )
            log.debug("Windows shortcut registration/dispatch failed: %s", exc)
            self._post(generation, self._publish, GlobalShortcutsState.ERROR, message)
        finally:
            if native is not None and registered:
                try:
                    native.unregister()
                except OSError as exc:
                    log.warning("Windows shortcut release failed: %s", exc)
