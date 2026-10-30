"""Windows input locales for the typing window, with verified switch requests."""

from __future__ import annotations

import ctypes
import os
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from docking.applets.keyboardlayout.backend import LayoutBackend
from docking.applets.keyboardlayout.state import LayoutInfo, LayoutState
from docking.log import get_logger
from docking.platform.backends.windows.ffi import bind
from docking.platform.backends.windows.input_context import (
    GuiThreadInfo as _GuiThreadInfo,
)
from docking.platform.backends.windows.input_context import (
    InputContext,
    typing_window,
)
from docking.platform.backends.windows.input_profiles import InputCatalog, InputProfile
from docking.platform.backends.windows.input_service import WindowsInputService
from docking.platform.backends.windows.input_thread import InputSnapshot, InputThread

log = get_logger("keyboardlayout.windows")
_DWORD = ctypes.c_uint32
_HWND = ctypes.c_void_p
_BOOL = ctypes.c_int32
_TRANSIENT_LANGIDS = frozenset((0x2000, 0x2400, 0x2800, 0x2C00))
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


def _code(handle: int) -> str:
    # Windows input locale IDs have two WORD components. Win64 may return a
    # sign-extended handle; both representations identify the same locale.
    return f"win32:{handle & 0xFFFFFFFF:08x}"


class KeyboardAPI(Protocol):
    def foreground(self) -> InputContext | None: ...
    def valid(self, context: InputContext) -> bool: ...
    def layouts(self) -> tuple[int, ...]: ...
    def active(self, thread: int) -> int: ...
    def describe(self, handle: int, *, tag: str = "") -> LayoutInfo: ...
    def describe_profile(self, profile: InputProfile, tag: str) -> LayoutInfo: ...
    def request(
        self, context: InputContext, handle: int, cancelled: threading.Event
    ) -> bool: ...
    def input_desktop_active(self) -> bool: ...
    def refresh(self, context: InputContext) -> InputContext | None: ...
    def focus(self, context: InputContext) -> bool: ...


class ProfilesAPI(Protocol):
    def catalog(self, loaded: tuple[int, ...] = ()) -> InputCatalog: ...
    def read(
        self, context: InputContext, cancelled: threading.Event
    ) -> InputSnapshot | None: ...
    def activate(
        self, context: InputContext, profile: InputProfile, cancelled: threading.Event
    ) -> InputSnapshot | None: ...


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
        self._input_thread = InputThread()
        self._locale = bind(
            kernel,
            "GetLocaleInfoW",
            ctypes.c_int,
            _DWORD,
            _DWORD,
            ctypes.c_wchar_p,
            ctypes.c_int,
        )
        self._locale_ex = bind(
            kernel,
            "GetLocaleInfoEx",
            ctypes.c_int,
            ctypes.c_wchar_p,
            _DWORD,
            ctypes.c_wchar_p,
            ctypes.c_int,
        )
        self._resolve_locale = bind(
            kernel,
            "ResolveLocaleName",
            ctypes.c_int,
            ctypes.c_wchar_p,
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
        window = typing_window(root, thread, self._gui, self._child)
        if window != root:
            thread = self._thread(window, ctypes.byref(pid))
            if not thread:
                return None
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
        if langid in _TRANSIENT_LANGIDS:
            return ""
        result = ctypes.create_unicode_buffer(256)
        return (
            str(result.value)
            if self._locale(langid, field, result, len(result))
            else ""
        )

    def describe(self, handle: int, *, tag: str = "") -> LayoutInfo:
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
        language, label = self._language_info(langid, tag)
        if not name:
            name = klid or _code(handle)
        return LayoutInfo(
            _code(handle), label, f"{language} — {name}" if language else name
        )

    def _language_info(self, language_id: int, tag: str) -> tuple[str, str]:
        language = self._locale_text(language_id, 2)
        label = self._locale_text(language_id, 0x59).upper()[:2]
        # Transient LANGIDs are not stable language identities. Resolve the
        # tag read on the target's input thread, without activating anything.
        if tag:
            resolved = ctypes.create_unicode_buffer(85)
            if self._resolve_locale(tag, resolved, len(resolved)):

                def text(field: int) -> str:
                    result = ctypes.create_unicode_buffer(256)
                    return (
                        str(result.value)
                        if self._locale_ex(resolved, field, result, len(result))
                        else ""
                    )

                language = text(2) or language
                label = text(0x59).upper()[:2] or label
        return language, label or "??"

    def describe_profile(self, profile: InputProfile, tag: str) -> LayoutInfo:
        language, label = self._language_info(profile.language, tag)
        name = profile.name or profile.code
        return LayoutInfo(
            profile.code,
            label or "??",
            f"{language} — {name}" if language else name,
        )

    def request(
        self, context: InputContext, handle: int, cancelled: threading.Event
    ) -> bool:
        if cancelled.is_set() or handle & 0xFFFFFFFF <= 1:
            return False
        try:
            snapshot = self._input_thread.request(context, cancelled, layout=handle)
        except OSError as exc:
            log.debug("Keyboard-layout switch unavailable: %s", exc)
            return False
        return (
            snapshot is not None
            and not cancelled.is_set()
            and _code(snapshot.layout) == _code(handle)
        )

    def focus(self, context: InputContext) -> bool:
        return bool(self._set_foreground(context.root))


@lru_cache(maxsize=1)
def _native_api() -> WindowsKeyboardAPI:
    return WindowsKeyboardAPI()


class WindowsKeyboardBackend(LayoutBackend):
    name = "windows"

    def __init__(
        self, *, api: KeyboardAPI | None = None, profiles: ProfilesAPI | None = None
    ) -> None:
        self._api = api
        self._profiles = profiles
        self._native_profiles = api is None
        self._catalog = InputCatalog()
        self._profile_records: dict[str, InputProfile] = {}
        self._profile_target: tuple[InputContext, InputSnapshot] | None = None
        self._target: InputContext | None = None
        self._contexts: tuple[InputContext, ...] = ()
        self._details: dict[str, LayoutInfo] = {}
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    @property
    def api(self) -> KeyboardAPI:
        return self._api if self._api is not None else _native_api()

    @property
    def profiles(self) -> ProfilesAPI | None:
        if self._profiles is None and self._native_profiles:
            self._profiles = WindowsInputService()
        return self._profiles

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
        # A target can change layouts while the worker reads TSF metadata. Keep
        # the published HKL, profile and available list from one stable sample.
        for _ in range(2):
            state = self._query_once(describe=describe)
            if state is not None:
                return state
        return LayoutState("", [])

    def _query_once(self, *, describe: bool) -> LayoutState | None:
        if self._stopped.is_set():
            return LayoutState("", [])
        context = self._context()
        if context is None:
            return LayoutState("", [])
        loaded = self.api.layouts()
        active = self.api.active(context.thread)
        loaded_codes = {_code(handle) for handle in loaded}
        if not active or _code(active) not in loaded_codes:
            return None
        catalog = self._catalog
        profile_records = self._profile_records
        profile_target = self._profile_target
        details = dict(self._details)
        if describe:
            details = {}
            for handle in loaded:
                code = _code(handle)
                details[code] = self._details.get(code) or self.api.describe(handle)
            try:
                profiles = self.profiles
                catalog = profiles.catalog(loaded) if profiles else InputCatalog()
                profile_records = {p.code: p for p in catalog.profiles}
                snapshot = (
                    profiles.read(context, self._stopped)
                    if profiles
                    and (profile_records or active & 0xFFFF in _TRANSIENT_LANGIDS)
                    else None
                )
                if snapshot is not None and _code(snapshot.layout) != _code(active):
                    return None
                profile_target = (context, snapshot) if snapshot else None
            except OSError as exc:
                profile_target = None
                log.debug("Input profiles unavailable: %s", exc)
            for code, profile in profile_records.items():
                tag = ""
                if profile_target is not None:
                    snapshot = profile_target[1]
                    if snapshot.profile and snapshot.profile.code == code:
                        tag = snapshot.language_tag
                details[code] = self.api.describe_profile(profile, tag)
            if (
                profile_target is not None
                and active & 0xFFFF in _TRANSIENT_LANGIDS
                and _code(profile_target[1].layout) == _code(active)
            ):
                snapshot = profile_target[1]
                if snapshot.language_tag:
                    details[_code(active)] = self.api.describe(
                        active, tag=snapshot.language_tag
                    )
        refreshed_loaded = self.api.layouts()
        active_code = _code(self.api.active(context.thread))
        if (
            active_code != _code(active)
            or {_code(handle) for handle in refreshed_loaded} != loaded_codes
        ):
            return None
        if profile_target is not None:
            saved, snapshot = profile_target
            if (
                saved == context
                and _code(snapshot.layout) == active_code
                and snapshot.profile is not None
                and snapshot.profile.code in profile_records
            ):
                active_code = snapshot.profile.code
        synthetic_layouts = catalog.synthetic_layouts(loaded)
        available = list(
            dict.fromkeys(
                [
                    _code(handle)
                    for handle in loaded
                    if _code(handle) not in synthetic_layouts
                    or _code(handle) == active_code
                ]
                + list(profile_records)
            )
        )
        if self._stopped.is_set() or not self.api.valid(context):
            return LayoutState("", [])
        if describe:
            self._catalog = catalog
            self._profile_records = profile_records
            self._profile_target = profile_target
            self._details = details
        return LayoutState(
            active_code,
            available,
            tuple(details[code] for code in available if code in details),
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
            if layout_code.startswith("tsf:"):
                profiles = self.profiles
                if profiles is None:
                    return
                try:
                    profile = next(
                        (
                            p
                            for p in profiles.catalog().profiles
                            if p.code == layout_code
                        ),
                        None,
                    )
                    if profile is None or self._stopped.is_set():
                        return
                    target = self._focus_target(target)
                    if target is None:
                        return
                    snapshot = profiles.activate(target, profile, self._stopped)
                    if (
                        snapshot is not None
                        and snapshot.profile is not None
                        and snapshot.profile.code == layout_code
                        and self.api.valid(target)
                        and not self._stopped.is_set()
                    ):
                        self._profile_target = (target, snapshot)
                except OSError as exc:
                    log.debug("Input-profile switch unavailable: %s", exc)
                return
            loaded = {_code(handle): handle for handle in self.api.layouts()}
            handle = loaded.get(layout_code)
            if handle is None or not self.api.valid(target) or self._stopped.is_set():
                return
            profiles = self.profiles
            catalog = self._catalog
            if profiles is not None:
                try:
                    catalog = profiles.catalog(tuple(loaded.values()))
                except OSError as exc:
                    log.debug("Keyboard-profile catalog unavailable: %s", exc)
            if layout_code in catalog.synthetic_layouts(tuple(loaded.values())):
                return
            target = self._focus_target(target)
            if target is None or layout_code not in {
                _code(h) for h in self.api.layouts()
            }:
                return
            if (
                self._profile_target is not None
                and self._profile_target[1].profile is not None
                and self._profile_target[1].profile.kind == 1
            ):
                if profiles is not None:
                    try:
                        keyboard = next(
                            (
                                p
                                for p in catalog.keyboards
                                if p.code == layout_code and p.enabled
                            ),
                            None,
                        )
                        if keyboard is not None:
                            snapshot = profiles.activate(
                                target, keyboard, self._stopped
                            )
                            if (
                                snapshot is not None
                                and snapshot.profile is not None
                                and snapshot.profile.code == layout_code
                            ):
                                self._profile_target = (target, snapshot)
                                return
                    except OSError as exc:
                        log.debug("Keyboard-profile switch unavailable: %s", exc)
                target = self._focus_target(target)
                if target is None:
                    return
            if (
                self._stopped.is_set()
                or not self.api.valid(target)
                or layout_code not in {_code(h) for h in self.api.layouts()}
            ):
                return
            if not self.api.request(target, handle, self._stopped):
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
        if self._stopped.is_set() or layout_code not in (
            {_code(h) for h in self.api.layouts()} | set(self._profile_records)
        ):
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
