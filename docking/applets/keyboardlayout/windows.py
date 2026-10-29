"""Windows input locales for the typing window, with verified switch requests."""

from __future__ import annotations

import ctypes
import os
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from docking.applets.keyboardlayout.backend import LayoutBackend
from docking.applets.keyboardlayout.state import LayoutInfo, LayoutState
from docking.log import get_logger
from docking.platform.backends.windows.ffi import bind

log = get_logger("keyboardlayout.windows")
_DWORD = ctypes.c_uint32
_HWND = ctypes.c_void_p
_BOOL = ctypes.c_int32
_LAYOUTS_KEY = "SYSTEM\\CurrentControlSet\\Control\\Keyboard Layouts"


def _layout_id(handle: int) -> str:
    """Resolve a physical layout without changing any thread's input locale."""
    device = (handle >> 16) & 0xFFFF
    if device & 0xF000 != 0xF000:
        # IME identifiers retain both WORDs; ordinary layouts use the device
        # language, which can differ from the input language in the low WORD.
        value = handle & 0xFFFFFFFF if device & 0xF000 == 0xE000 else device
        return f"{value or (handle & 0xFFFF):08X}"

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _LAYOUTS_KEY) as layouts:
            for index in range(winreg.QueryInfoKey(layouts)[0]):
                klid = str(winreg.EnumKey(layouts, index))
                if not re.fullmatch(r"[0-9a-fA-F]{8}", klid):
                    continue
                try:
                    with winreg.OpenKey(layouts, klid) as key:
                        layout_id, _ = winreg.QueryValueEx(key, "Layout Id")
                    if not isinstance(layout_id, str):
                        continue
                    if int(layout_id, 16) == device & 0x0FFF:
                        return klid.upper()
                except (OSError, ValueError):
                    continue
    except OSError as exc:
        log.debug("Keyboard layout identity unavailable: %s", exc)
    return ""


class _Rect(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in ("left", "top", "right", "bottom")]


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = (
        [("size", _DWORD), ("flags", _DWORD)]
        + [
            (name, _HWND)
            for name in ("active", "focus", "capture", "menu", "move", "caret")
        ]
        + [("caret_rect", _Rect)]
    )


def _code(handle: int) -> str:
    # Windows input locale IDs have two WORD components. Win64 may return a
    # sign-extended handle; both representations identify the same locale.
    return f"win32:{handle & 0xFFFFFFFF:08x}"


@dataclass(frozen=True)
class InputContext:
    root: int
    window: int
    thread: int
    pid: int
    control_surface: bool = False
    root_thread: int = 0
    root_pid: int = 0

    @property
    def key(self) -> str:
        thread, pid = self.root_thread or self.thread, self.root_pid or self.pid
        return f"{self.root:x}:{thread:x}:{pid:x}"


class KeyboardAPI(Protocol):
    def foreground(self) -> InputContext | None: ...
    def valid(self, context: InputContext) -> bool: ...
    def layouts(self) -> tuple[int, ...]: ...
    def active(self, thread: int) -> int: ...
    def describe(self, handle: int) -> LayoutInfo: ...
    def request(self, context: InputContext, handle: int) -> bool: ...
    def input_desktop_active(self) -> bool: ...
    def refresh(self, context: InputContext) -> InputContext | None: ...
    def focus(self, context: InputContext) -> bool: ...


class WindowsKeyboardAPI:
    def __init__(self) -> None:
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32")
        self._foreground = bind(user, "GetForegroundWindow", _HWND)
        self._set_foreground = bind(user, "SetForegroundWindow", _BOOL, _HWND)
        self._thread = bind(
            user, "GetWindowThreadProcessId", _DWORD, _HWND, ctypes.POINTER(_DWORD)
        )
        self._gui = bind(
            user, "GetGUIThreadInfo", _BOOL, _DWORD, ctypes.POINTER(_GuiThreadInfo)
        )
        self._window = bind(user, "IsWindow", _BOOL, _HWND)
        self._visible = bind(user, "IsWindowVisible", _BOOL, _HWND)
        self._child = bind(user, "IsChild", _BOOL, _HWND, _HWND)
        self._text = bind(
            user, "GetWindowTextW", ctypes.c_int, _HWND, ctypes.c_wchar_p, ctypes.c_int
        )
        self._class = bind(
            user, "GetClassNameW", ctypes.c_int, _HWND, ctypes.c_wchar_p, ctypes.c_int
        )
        self._list = bind(
            user,
            "GetKeyboardLayoutList",
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(_HWND),
        )
        self._active = bind(user, "GetKeyboardLayout", _HWND, _DWORD)
        self._post = bind(
            user,
            "PostMessageW",
            _BOOL,
            _HWND,
            _DWORD,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )
        self._locale = bind(
            kernel,
            "GetLocaleInfoW",
            ctypes.c_int,
            _DWORD,
            _DWORD,
            ctypes.c_wchar_p,
            ctypes.c_int,
        )
        self._indirect = bind(
            ctypes.WinDLL("shlwapi"),
            "SHLoadIndirectString",
            ctypes.c_int32,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            _DWORD,
            ctypes.c_void_p,
        )
        self._input_desktop = bind(
            user, "OpenInputDesktop", ctypes.c_void_p, _DWORD, _BOOL, _DWORD
        )
        self._close_desktop = bind(user, "CloseDesktop", _BOOL, ctypes.c_void_p)
        self._thread_desktop = bind(user, "GetThreadDesktop", ctypes.c_void_p, _DWORD)
        self._thread_id = bind(kernel, "GetCurrentThreadId", _DWORD)
        self._object_info = bind(
            user,
            "GetUserObjectInformationW",
            _BOOL,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            _DWORD,
            ctypes.POINTER(_DWORD),
        )

    def input_desktop_active(self) -> bool:
        current = self._input_desktop(0, False, 1)  # DESKTOP_READOBJECTS
        if not current:
            return False

        def name(handle):
            text = ctypes.create_unicode_buffer(256)
            needed = _DWORD()
            return (
                str(text.value)
                if self._object_info(
                    handle, 2, text, ctypes.sizeof(text), ctypes.byref(needed)
                )
                else ""
            )

        try:
            receiving_input = _BOOL()
            needed = _DWORD()
            if (
                not self._object_info(
                    current,
                    6,
                    ctypes.byref(receiving_input),
                    ctypes.sizeof(receiving_input),
                    ctypes.byref(needed),
                )
                or not receiving_input.value
            ):
                return False
            own = self._thread_desktop(self._thread_id())
            own_name = name(own) if own else ""
            return bool(own_name) and own_name == name(current)
        finally:
            self._close_desktop(current)

    def foreground(self) -> InputContext | None:
        root = self._foreground()
        return self._context_for_root(root) if root else None

    def refresh(self, context: InputContext) -> InputContext | None:
        current = self._context_for_root(context.root)
        if current is None or current.key != context.key:
            return None
        # A thread loses hwndFocus when the dock temporarily takes activation.
        # Keep its still-live editing window instead of changing the target to
        # the application root's different UI thread.
        if (
            current.window == current.root
            and context.window != context.root
            and self.valid(context)
        ):
            return context
        return current

    def _context_for_root(self, root: int) -> InputContext | None:
        if not root or not self._visible(root):
            return None
        pid = _DWORD()
        thread = self._thread(root, ctypes.byref(pid))
        if not thread:
            return None
        root_thread, root_pid = int(thread), int(pid.value)
        info = _GuiThreadInfo()
        info.size = ctypes.sizeof(info)
        window = root
        if (
            self._gui(thread, ctypes.byref(info))
            and info.focus
            and (info.focus == root or self._child(root, info.focus))
        ):
            window = info.focus
            thread = self._thread(window, ctypes.byref(pid))
        title, kind = (
            ctypes.create_unicode_buffer(256),
            ctypes.create_unicode_buffer(256),
        )
        self._text(root, title, len(title))
        self._class(root, kind, len(kind))
        # Ignore only the dock and its temporary menus, not the user's typing
        # windows such as Docking Search or Preferences.
        surface = root_pid == os.getpid() and (
            title.value == "Docking" or kind.value.startswith("gdkWindowTemp")
        )
        return InputContext(
            root, window, thread, pid.value, surface, root_thread, root_pid
        )

    def valid(self, context: InputContext) -> bool:
        if not self._window(context.root) or not self._window(context.window):
            return False
        if context.window != context.root and not self._child(
            context.root, context.window
        ):
            return False
        pid = _DWORD()
        root_thread = self._thread(context.root, ctypes.byref(pid))
        if (root_thread, pid.value) != (
            context.root_thread or context.thread,
            context.root_pid or context.pid,
        ):
            return False
        thread = self._thread(context.window, ctypes.byref(pid))
        return (thread, pid.value) == (context.thread, context.pid)

    def layouts(self) -> tuple[int, ...]:
        for _ in range(3):
            count = self._list(0, None)
            if count <= 0 or count > 256:
                return ()
            values = (_HWND * count)()
            copied = self._list(count, values)
            if self._list(0, None) == copied:
                return tuple(
                    dict.fromkeys(int(value) for value in values[:copied] if value)
                )
        return ()

    def active(self, thread: int) -> int:
        return int(self._active(thread) or 0)

    def _locale_text(self, langid: int, field: int) -> str:
        result = ctypes.create_unicode_buffer(256)
        return (
            str(result.value)
            if self._locale(langid, field, result, len(result))
            else ""
        )

    def describe(self, handle: int) -> LayoutInfo:
        # Activating layouts even on a worker can interrupt foreground dead-key
        # composition. Metadata reads must leave the input state untouched.
        klid = _layout_id(handle)
        name = ""
        if re.fullmatch(r"[0-9a-fA-F]{8}", klid):
            import winreg

            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    _LAYOUTS_KEY + "\\" + klid,
                ) as key:
                    try:
                        resource, _ = winreg.QueryValueEx(key, "Layout Display Name")
                        buffer = ctypes.create_unicode_buffer(512)
                        if (
                            self._indirect(
                                os.path.expandvars(resource), buffer, len(buffer), None
                            )
                            == 0
                        ):
                            name = buffer.value
                    except OSError:
                        pass
                    if not name:
                        name, _ = winreg.QueryValueEx(key, "Layout Text")
            except OSError as exc:
                log.debug("Keyboard description unavailable: %s", exc)
        langid = handle & 0xFFFF
        language = self._locale_text(langid, 2)
        label = self._locale_text(langid, 0x59).upper()[:2] or "??"
        if not name:
            name = klid or _code(handle)
        return LayoutInfo(
            _code(handle), label, f"{language} — {name}" if language else name
        )

    def request(self, context: InputContext, handle: int) -> bool:
        return bool(self._post(context.window, 0x50, 0, ctypes.c_ssize_t(handle).value))

    def focus(self, context: InputContext) -> bool:
        return bool(self._set_foreground(context.root))


@lru_cache(maxsize=1)
def _native_api() -> WindowsKeyboardAPI:
    return WindowsKeyboardAPI()


class WindowsKeyboardBackend(LayoutBackend):
    name = "windows"

    def __init__(self, *, api: KeyboardAPI | None = None) -> None:
        self._api = api
        self._target: InputContext | None = None
        self._contexts: tuple[InputContext, ...] = ()
        self._details: dict[str, LayoutInfo] = {}
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    @property
    def api(self) -> KeyboardAPI:
        return self._api if self._api is not None else _native_api()

    def _context(self) -> InputContext | None:
        if not self.api.input_desktop_active():
            return None
        current = self.api.foreground()
        # GDK's non-activating mouse handling can clear foreground ownership.
        # Keep a bounded history of typing windows on the active input desktop.
        # A hidden Search window must not replace the preceding visible source
        # during the native foreground gap caused by opening a dock menu.
        if current is not None and not current.control_surface:
            recent = tuple(
                saved for saved in self._contexts if saved.key != current.key
            )[-7:]
            self._contexts = (*recent, current)
        self._target = None
        for saved in reversed(self._contexts):
            refreshed = self.api.refresh(saved)
            if refreshed is not None and self.api.valid(refreshed):
                self._target = refreshed
                break
        return self._target

    def _query(self, *, describe: bool) -> LayoutState:
        if self._stopped.is_set():
            return LayoutState("", [])
        context = self._context()
        if context is None:
            return LayoutState("", [])
        loaded = self.api.layouts()
        active = self.api.active(context.thread)
        if not active or _code(active) not in {_code(handle) for handle in loaded}:
            return LayoutState("", [])
        details = dict(self._details)
        if describe:
            details = {}
            for handle in loaded:
                code = _code(handle)
                details[code] = self._details.get(code) or self.api.describe(handle)
            self._details = details
        if self._stopped.is_set() or not self.api.valid(context):
            return LayoutState("", [])
        return LayoutState(
            _code(active),
            [_code(handle) for handle in loaded],
            tuple(
                details[code] for handle in loaded if (code := _code(handle)) in details
            ),
            context.key,
        )

    def is_available(self) -> bool:
        return bool(self.query().available)

    def query(self) -> LayoutState:
        with self._lock:
            return self._query(describe=True)

    def prepare_interaction(self, state: LayoutState) -> LayoutState:
        # Fast Win32 reads only; no command, COM call or locale activation on
        # the GTK thread. This captures the target before a menu takes focus.
        return self._query(describe=False)

    def switch(self, layout_code: str, *, context: str = "") -> None:
        with self._lock:
            if self._stopped.is_set():
                return
            target = self._context()
            if target is None or target.key != context:
                return
            loaded = {_code(handle): handle for handle in self.api.layouts()}
            handle = loaded.get(layout_code)
            if handle is None or not self.api.valid(target) or self._stopped.is_set():
                return
            target = self._focus_target(target)
            if target is None or layout_code not in {
                _code(h) for h in self.api.layouts()
            }:
                return
            if not self.api.request(target, handle):
                return
            deadline = time.monotonic() + 0.8
            while not self._stopped.is_set() and time.monotonic() < deadline:
                if (
                    not self.api.valid(target)
                    or _code(self.api.active(target.thread)) == layout_code
                ):
                    break
                self._stopped.wait(0.02)

    def _focus_target(self, target: InputContext) -> InputContext | None:
        # Apply after typing-focus restoration. A WinUI editing thread can
        # otherwise restore its previous input locale when it regains focus.
        deadline = time.monotonic() + 0.7
        expected_editor = target.window != target.root
        stable = None
        while not self._stopped.is_set() and time.monotonic() < deadline:
            refreshed = self.api.refresh(target)
            if (
                refreshed is None
                or not self.api.input_desktop_active()
                or not self.api.valid(refreshed)
            ):
                return None
            current = self.api.foreground()
            if current is not None and not current.control_surface:
                if current.key != target.key:
                    return None
                identity = (current.window, current.thread, current.pid)
                if (
                    not expected_editor or current.window != current.root
                ) and identity == stable:
                    return current
                stable = identity
            else:
                stable = None
                self.api.focus(refreshed)
            self._stopped.wait(0.02)
        return None

    def settings_available(self) -> bool:
        return True

    def viewer_available(self, layout_code: str) -> bool:
        return bool(layout_code)

    def open_settings(self) -> bool:
        if self._stopped.is_set():
            return False
        try:
            os.startfile("ms-settings:typing")
            return True
        except OSError as exc:
            log.debug("Keyboard Settings unavailable: %s", exc)
            return False

    def show_layout(self, layout_code: str) -> bool:
        if self._stopped.is_set() or layout_code not in {
            _code(h) for h in self.api.layouts()
        }:
            return False
        try:
            os.startfile(
                str(
                    Path(os.environ.get("SYSTEMROOT", "C:\\Windows"))
                    / "System32/osk.exe"
                )
            )
            return True
        except OSError as exc:
            log.debug("On-Screen Keyboard unavailable: %s", exc)
            return False

    def stop(self) -> None:
        self._stopped.set()
