"""Windows input-locale identity, foreground ownership and switch readback."""

import ctypes
import sys
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.keyboardlayout.implementations.windows import backend as mod
from docking.applets.keyboardlayout.state import (
    LayoutInfo,
    LayoutState,
    layout_label,
    tooltip_text,
)


class KeyboardAPI:
    def __init__(self):
        self.window = mod.InputContext(101, 102, 201, 301)
        self.alive = True
        self.loaded = (0x04090409, 0xF0020409, 0x040C040C)
        self.current = self.loaded[0]
        self.accept = True
        self.desktop_active = True
        self.requests = []
        self.described = []

    def foreground(self):
        return self.window

    def input_desktop_active(self):
        return self.desktop_active

    def refresh(self, context):
        return context if self.alive else None

    def focus(self, context):
        self.window = context
        return True

    def valid(self, context):
        return self.alive

    def layouts(self):
        return self.loaded

    def active(self, thread):
        return self.current

    def describe(self, handle, *, tag=""):
        self.described.append(handle)
        label, name = {
            self.loaded[0]: ("EN", "US"),
            self.loaded[1]: ("EN", "US-Dvorak"),
            self.loaded[2]: ("FR", "French"),
        }.get(handle, ("??", "Unknown"))
        return LayoutInfo(mod._code(handle), label, name)

    def request(self, context, handle, cancelled):
        assert not cancelled.is_set()
        self.requests.append((context, handle))
        if self.accept:
            self.current = handle
        return self.accept


@pytest.fixture
def keyboard():
    api = KeyboardAPI()
    return mod.WindowsKeyboardBackend(api=api), api


def test_same_language_variants_keep_distinct_identities_and_descriptions(keyboard):
    backend, api = keyboard
    state = backend.query()
    assert state.available == [mod._code(h) for h in api.loaded]
    assert (
        layout_label(state.available[0], state)
        == layout_label(state.available[1], state)
        == "EN"
    )
    assert tooltip_text(state.available[1], state) == "US-Dvorak"
    backend.switch(state.available[1], context=state.context)
    assert backend.query().active == state.available[1]
    assert api.requests == [(api.window, 0xF0020409)]


def test_sign_extended_handles_have_the_same_session_identity():
    assert mod._code(0xFFFFFFFFF0020409) == mod._code(0xF0020409)


@pytest.fixture
def layout_registry(monkeypatch):
    values = {
        "00000409": {"Layout Text": "US"},
        "0000040C": {"Layout Text": "French"},
        "00010409": {"Layout Id": "0002", "Layout Text": "US-Dvorak"},
    }

    @contextmanager
    def opened(hive, path):
        if hive == 1:
            assert path == mod._LAYOUTS_KEY or path.startswith(mod._LAYOUTS_KEY + "\\")
        elif hive != mod._LAYOUTS_KEY:
            raise FileNotFoundError(path)
        key = path if path == mod._LAYOUTS_KEY else path.rsplit("\\", 1)[-1]
        if key != mod._LAYOUTS_KEY and key not in values:
            raise FileNotFoundError(key)
        yield key

    def query(key, name):
        try:
            return values[key][name], 1
        except KeyError as exc:
            raise FileNotFoundError(name) from exc

    monkeypatch.setitem(
        sys.modules,
        "winreg",
        SimpleNamespace(
            HKEY_LOCAL_MACHINE=1,
            OpenKey=opened,
            QueryInfoKey=lambda _key: (len(values), 0, 0),
            EnumKey=lambda _key, index: list(values)[index],
            QueryValueEx=query,
        ),
    )
    return values


@pytest.mark.parametrize(
    "handle,expected",
    [
        (0x04090409, "00000409"),
        (0x040C040C, "0000040C"),
        (0x040C0409, "0000040C"),
        (0x00000409, "00000409"),
        (0xF0020409, "00010409"),
        (0xFFFFFFFFF0020409, "00010409"),
        (0xF002040C, "00010409"),
        (0xE0010411, "E0010411"),
        (0xF1230409, ""),
    ],
)
def test_layout_identity_uses_device_and_registry_variant_id(
    layout_registry, handle, expected
):
    assert mod._layout_id(handle) == expected


def test_unreadable_and_malformed_registry_variants_keep_the_handle_identity(
    layout_registry, monkeypatch
):
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    api._locale_text = lambda _langid, field: "EN" if field == 0x59 else "English"
    layout_registry["00010409"]["Layout Id"] = "invalid"
    assert api.describe(0xF0020409).name == "English — win32:f0020409"
    layout_registry["00010409"]["Layout Id"] = 2
    assert mod._layout_id(0xF0020409) == ""
    registry = sys.modules["winreg"]
    monkeypatch.setattr(registry, "OpenKey", Mock(side_effect=PermissionError))
    assert mod._layout_id(0xF0020409) == ""


def test_native_descriptions_do_not_activate_layouts_or_query_thread_state(
    layout_registry,
):
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    api.active = Mock(side_effect=AssertionError("metadata queried input state"))
    api._activate = Mock(side_effect=AssertionError("metadata activated a layout"))
    api._locale_text = lambda _langid, field: "EN" if field == 0x59 else "English"
    us = api.describe(0x04090409)
    dvorak = api.describe(0xF0020409)
    assert us.name == "English — US"
    assert dvorak.name == "English — US-Dvorak"
    api.active.assert_not_called()
    api._activate.assert_not_called()


def test_metadata_cache_tracks_only_loaded_layouts_and_describes_new_arrivals(keyboard):
    backend, api = keyboard
    original = api.loaded
    backend.query()
    assert api.described == list(original)
    api.described.clear()
    backend.query()
    assert api.described == []
    api.loaded = original[:2]
    backend.query()
    api.loaded = original
    backend.query()
    assert api.described == [original[2]]


@pytest.mark.parametrize("handle", [0x040C040C, 0xFFFFFFFFF0020409])
def test_layout_request_uses_cancellable_target_thread_and_normalized_readback(handle):
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    api._input_thread = Mock()
    api._input_thread.request.return_value = mod.InputSnapshot(
        None, handle & 0xFFFFFFFF, "", False, False, -1, 0, 0
    )
    context = mod.InputContext(100, 101, 201, 301)
    cancelled = threading.Event()
    assert api.request(context, handle, cancelled)
    api._input_thread.request.assert_called_once_with(context, cancelled, layout=handle)


@pytest.mark.parametrize("failure", ["none", "different", "cancelled", "error"])
def test_layout_request_never_reports_an_unverified_or_cancelled_switch(failure):
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    cancelled = threading.Event()
    snapshot = mod.InputSnapshot(None, 0x040C040C, "", False, False, -1, 0, 0)

    def request(context, event, *, layout):
        assert event is cancelled and layout == 0x040C040C
        if failure == "error":
            raise PermissionError("Denied input thread")
        if failure == "cancelled":
            event.set()
        if failure == "different":
            return mod.InputSnapshot(None, 0x04090409, "", False, False, -1, 0, 0)
        return None if failure == "none" else snapshot

    api._input_thread = Mock(request=Mock(side_effect=request))
    assert not api.request(mod.InputContext(100, 101, 201, 301), 0x040C040C, cancelled)


def test_cancelled_layout_request_does_not_initialize_a_native_helper():
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    cancelled = threading.Event()
    cancelled.set()
    assert not api.request(mod.InputContext(100, 101, 201, 301), 0x040C040C, cancelled)


@pytest.mark.parametrize("handle", [0, 1, 0x100000000])
def test_layout_request_rejects_next_previous_and_truncated_zero_handles(handle):
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    assert not api.request(
        mod.InputContext(100, 101, 201, 301), handle, threading.Event()
    )


def test_refresh_keeps_live_editing_thread_while_root_temporarily_has_no_focus():
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    root = mod.InputContext(100, 100, 200, 300, root_thread=200, root_pid=300)
    editor = mod.InputContext(100, 101, 201, 300, root_thread=200, root_pid=300)
    api._context_for_root = lambda _root: root
    api.valid = lambda _context: True
    assert api.refresh(editor) == editor
    api.valid = lambda _context: False
    assert api.refresh(editor) == root


def test_denied_requests_keep_actual_source_and_do_not_change_other_layouts(keyboard):
    backend, api = keyboard
    state = backend.query()
    api.accept = False
    backend.switch(state.available[1], context=state.context)
    assert backend.query().active == state.active


@pytest.mark.parametrize(
    "reason",
    ["new-app", "destroyed", "removed-layout", "no-foreground", "reused-window"],
)
def test_stale_context_or_unloaded_source_is_never_switched(keyboard, reason):
    backend, api = keyboard
    state = backend.query()
    if reason == "new-app":
        api.window = mod.InputContext(999, 998, 997, 996)
    elif reason == "destroyed":
        api.alive = False
    elif reason == "removed-layout":
        api.loaded = (api.loaded[0], api.loaded[2])
    elif reason == "no-foreground":
        api.window = None
        api.desktop_active = False
    else:
        api.window = mod.InputContext(api.window.root, api.window.window, 444, 555)
    backend.switch(state.available[1], context=state.context)
    assert api.requests == []


def test_menu_handoff_retains_previous_input_window_but_search_can_be_targeted(
    keyboard,
):
    backend, api = keyboard
    state = backend.query()
    target = api.window
    api.window = mod.InputContext(900, 900, 901, 902, control_surface=True)
    backend.switch(state.available[1], context=state.context)
    assert api.requests == [(target, 0xF0020409)]
    # Search/Preferences are real typing windows, not dock/menu surfaces.
    api.window = mod.InputContext(1000, 1001, 1002, 1003)
    actual = backend.prepare_interaction(state)
    assert actual.context == api.window.key and actual.context != state.context


def test_hidden_search_context_restores_the_preceding_visible_typing_target(keyboard):
    backend, api = keyboard
    source = api.window
    source_state = backend.query()
    search = mod.InputContext(1000, 1001, 1002, 1003)
    api.window = search
    assert backend.query().context == search.key
    api.window = mod.InputContext(900, 900, 901, 902, control_surface=True)
    api.refresh = lambda context: None if context.key == search.key else context
    assert backend.prepare_interaction(source_state).context == source.key
    backend.switch(source_state.available[1], context=source.key)
    assert api.requests == [(source, 0xF0020409)]


def test_hidden_context_fallback_never_overrides_a_new_foreground_application(keyboard):
    backend, api = keyboard
    original = backend.query()
    search = mod.InputContext(1000, 1001, 1002, 1003)
    api.window = search
    backend.query()
    api.window = mod.InputContext(900, 900, 901, 902, control_surface=True)
    api.refresh = lambda context: None if context.key == search.key else context
    restored = backend.prepare_interaction(original)
    api.window = mod.InputContext(2000, 2001, 2002, 2003)
    backend.switch(restored.available[1], context=restored.context)
    assert api.requests == []


def test_native_context_rejects_hidden_windows_before_querying_typing_focus():
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    api._visible = Mock(return_value=False)

    def thread(_window, pointer):
        pointer._obj.value = 301
        return 201

    api._thread = Mock(side_effect=thread)
    api._gui = Mock(return_value=False)
    api._text = Mock(
        side_effect=lambda _window, text, _length: setattr(
            text, "value", "Docking Search"
        )
    )
    api._class = Mock(
        side_effect=lambda _window, text, _length: setattr(
            text, "value", "gdkWindowToplevel"
        )
    )
    assert api._context_for_root(100) is None
    api._thread.assert_not_called()


def test_interaction_snapshot_does_not_activate_or_describe_layouts_on_gtk_thread(
    keyboard,
):
    backend, api = keyboard
    state = backend.query()
    api.described.clear()
    api.current = api.loaded[2]
    updated = backend.prepare_interaction(state)
    assert updated.active == state.available[2]
    assert api.described == []


def test_foreground_gap_retains_last_typing_window_only_on_its_active_desktop(
    keyboard,
):
    backend, api = keyboard
    state = backend.query()
    api.window = None
    assert backend.query().context == state.context
    assert backend.prepare_interaction(state).context == state.context
    backend.switch(state.available[1], context=state.context)
    assert api.requests[0][1] == 0xF0020409

    api.requests.clear()
    api.desktop_active = False
    assert backend.query() == LayoutState("", [])
    backend.switch(state.available[0], context=state.context)
    assert api.requests == []
    api.window = mod.InputContext(900, 900, 901, 902, control_surface=True)
    api.desktop_active = True
    backend.switch(state.available[1], context=state.context)
    assert api.requests[0][1] == 0xF0020409


def test_menu_can_resolve_a_recreated_focus_proxy_in_the_same_application(keyboard):
    backend, api = keyboard
    state = backend.query()
    old = api.window
    api.window = mod.InputContext(900, 900, 901, 902, control_surface=True)
    refreshed = mod.InputContext(
        old.root,
        old.window + 1,
        old.thread,
        old.pid,
        root_thread=old.thread,
        root_pid=old.pid,
    )
    api.refresh = lambda context: refreshed
    assert refreshed.key == state.context
    backend.switch(state.available[1], context=state.context)
    assert api.requests == [(refreshed, 0xF0020409)]


def test_stopped_backend_prevents_queries_switches_and_launches(keyboard):
    backend, api = keyboard
    state = backend.query()
    backend.stop()
    api.foreground = lambda: pytest.fail("stopped backend queried Windows")
    assert backend.query() == LayoutState("", [])
    backend.switch(state.available[1], context=state.context)
    assert api.requests == []
    assert backend.open_settings() is False
    assert backend.show_layout(state.active) is False


def test_keyboard_list_retries_growth_and_rejects_an_unstable_snapshot():
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    counts = iter((1, 2, 2, 2))

    def read(count, values):
        if not count:
            return next(counts)
        values[0] = 0x04090409
        if count > 1:
            values[1] = 0x040C040C
        return count

    api._list = read
    assert api.layouts() == (0x04090409, 0x040C040C)
    counts = iter((1, 2, 1, 2, 1, 2))
    assert api.layouts() == ()


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("active", True),
        ("denied", False),
        ("secure", False),
        ("disconnected", False),
        ("query-failed", False),
    ],
)
def test_input_desktop_gate_releases_owned_handles_and_rejects_inactive_desktops(
    scenario, expected
):
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    closed = []
    api._input_desktop = lambda *args: 0 if scenario == "denied" else 100
    api._close_desktop = lambda handle: closed.append(handle) or True
    api._thread_id = lambda: 123
    api._thread_desktop = lambda thread: 200

    def information(handle, field, target, length, needed):
        if scenario == "query-failed":
            return False
        if field == 6:
            target._obj.value = int(scenario != "disconnected")
        else:
            target.value = (
                "Winlogon" if handle == 100 and scenario == "secure" else "Default"
            )
        return True

    api._object_info = information
    assert api.input_desktop_active() is expected
    assert closed == ([] if scenario == "denied" else [100])


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows ABI and real input-locale query"
)
def test_native_read_cycles_preserve_foreground_layout_loaded_set_and_handles(
    native_keyboard_window,
):
    import threading

    api, pump = native_keyboard_window
    assert ctypes.sizeof(mod._GuiThreadInfo) == 72
    before_context = api.foreground()
    assert before_context is not None
    before_loaded = set(api.layouts())
    before_active = api.active(before_context.thread)
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessHandleCount.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    )

    def handles():
        count = ctypes.c_uint32()
        assert kernel.GetProcessHandleCount(
            kernel.GetCurrentProcess(), ctypes.byref(count)
        )
        return count.value

    backend = mod.WindowsKeyboardBackend(api=api)
    errors = []

    def read():
        try:
            for _ in range(20):
                state = backend.query()
                assert state.active and state.active in state.available
                assert all(info.label and info.name for info in state.details)
        except BaseException as exc:
            errors.append(exc)

    before_handles = handles()
    worker = threading.Thread(target=read)
    worker.start()
    deadline = time.monotonic() + 20
    while worker.is_alive() and time.monotonic() < deadline:
        pump()
        worker.join(0.01)
    assert not worker.is_alive() and not errors
    assert set(api.layouts()) == before_loaded
    assert api.active(before_context.thread) == before_active
    assert handles() <= before_handles + 2
    backend.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="Requires a native Windows desktop")
def test_native_classic_request_accepts_an_initially_uninitialized_com_thread():
    from docking.platform.native.windows.com import HRESULT
    from docking.platform.native.windows.ffi import bind

    ready, finished = threading.Event(), threading.Event()
    shared, errors = {}, []

    def host():
        try:
            apartment_type = bind(
                ctypes.WinDLL("ole32"),
                "CoGetApartmentType",
                HRESULT,
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
            )

            def uninitialized():
                kind, qualifier = ctypes.c_int(), ctypes.c_int()
                return (
                    apartment_type(ctypes.byref(kind), ctypes.byref(qualifier))
                    & 0xFFFFFFFF
                    == 0x800401F0  # CO_E_NOTINITIALIZED
                )

            if not uninitialized():
                pytest.skip("The process already provides an implicit COM apartment")
            with _native_keyboard_window() as (api, pump):
                context = api.foreground()
                assert context is not None
                if not uninitialized():
                    pytest.skip("Native window setup supplied a COM apartment")
                shared.update(api=api, context=context)
                ready.set()
                deadline = time.monotonic() + 25
                while not finished.is_set() and time.monotonic() < deadline:
                    pump()
                    finished.wait(0.005)
                assert finished.is_set(), (
                    "Controller did not finish the classic request"
                )
                # Windows owns any COM state its native layout APIs establish.
        except BaseException as error:
            errors.append(error)
        finally:
            ready.set()

    worker = threading.Thread(target=host, daemon=True)
    worker.start()
    try:
        assert ready.wait(20), "Owned non-COM window did not map"
        if errors:
            raise errors[0]
        api, context = shared["api"], shared["context"]
        loaded = api.layouts()
        original, caller = api.active(context.thread), api.active(0)
        wanted = next(
            (h for h in loaded if mod._code(h) != mod._code(original)), original
        )
        try:
            assert api.request(context, original, threading.Event())
            assert api.request(context, wanted, threading.Event())
            assert mod._code(api.active(context.thread)) == mod._code(wanted)
            assert api.active(0) == caller
            assert set(api.layouts()) == set(loaded)
        finally:
            assert api.request(context, original, threading.Event())
    finally:
        finished.set()
        worker.join(10)
    assert not worker.is_alive(), "Owned input thread did not finish"
    if errors:
        raise errors[0]


@pytest.fixture
def native_keyboard_window():
    with _native_keyboard_window() as native:
        yield native


@contextmanager
def _native_keyboard_window():
    """Own a Win32 input window without measuring unrelated GTK initialization."""
    from docking.platform.native.windows.ffi import bind

    api = mod.WindowsKeyboardAPI()
    previous = api.foreground()
    user = ctypes.WinDLL("user32", use_last_error=True)
    cursor = wintypes.POINT()
    get_cursor = bind(
        user, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(wintypes.POINT)
    )
    assert get_cursor(ctypes.byref(cursor))
    set_cursor = bind(user, "SetCursorPos", wintypes.BOOL, ctypes.c_int, ctypes.c_int)
    rectangle = bind(
        user,
        "GetWindowRect",
        wintypes.BOOL,
        wintypes.HWND,
        ctypes.POINTER(wintypes.RECT),
    )
    mouse = bind(
        user,
        "mouse_event",
        None,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    )
    create = bind(
        user,
        "CreateWindowExW",
        wintypes.HWND,
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        ctypes.c_void_p,
    )
    destroy = bind(user, "DestroyWindow", wintypes.BOOL, wintypes.HWND)
    peek = bind(
        user,
        "PeekMessageW",
        wintypes.BOOL,
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    )
    translate = bind(
        user, "TranslateMessage", wintypes.BOOL, ctypes.POINTER(wintypes.MSG)
    )
    dispatch = bind(
        user, "DispatchMessageW", ctypes.c_ssize_t, ctypes.POINTER(wintypes.MSG)
    )
    # Built-in window classes need no registered procedure or callback lifetime.
    handle = create(
        8,
        "STATIC",
        "Docking keyboard test",
        0x10CF0000,
        100,
        100,
        300,
        150,
        None,
        None,
        None,
        None,
    )
    assert handle

    def pump():
        message = wintypes.MSG()
        while peek(ctypes.byref(message), handle, 0, 0, 1):
            translate(ctypes.byref(message))
            dispatch(ctypes.byref(message))

    try:
        edit = create(
            0x200, "EDIT", "", 0x50010080, 10, 10, 260, 30, handle, None, None, None
        )
        assert edit
        pump()
        bounds = wintypes.RECT()
        assert rectangle(handle, ctypes.byref(bounds))
        # Windows may deny programmatic foreground activation for a background
        # test runner. Actual input to our own visible window establishes focus.
        assert set_cursor(
            (bounds.left + bounds.right) // 2, (bounds.top + bounds.bottom) // 2
        )
        mouse(2, 0, 0, 0, 0)
        mouse(4, 0, 0, 0, 0)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pump()
            context = api.foreground()
            if context is not None and context.root == handle:
                break
            time.sleep(0.01)
        assert context is not None and context.root == handle
        yield api, pump
    finally:
        assert destroy(handle)
        set_cursor(cursor.x, cursor.y)
        if previous is not None and api.valid(previous):
            api.focus(previous)


@pytest.fixture
def text_profiles():
    from dataclasses import replace

    from docking.platform.native.windows.input_profiles import (
        KEYBOARD_CATEGORY,
        InputCatalog,
        InputProfile,
    )
    from docking.platform.native.windows.input_thread import InputSnapshot

    first = InputProfile(
        1,
        0x42A,
        "c2cb2cf0-af47-413e-9780-8bc3a3c16068",
        "5fb02ec5-0a77-4684-b4fa-def8a2195628",
        KEYBOARD_CATEGORY,
        0,
        0,
        2,
        "Telex",
    )
    second = replace(
        first, identifier="591ae943-56be-48f6-8966-06b43915cc5a", name="VNI"
    )
    api = KeyboardAPI()
    api.current = 0x042A042A
    api.loaded = (*api.loaded, api.current)
    api.describe_profile = lambda p, tag: LayoutInfo(p.code, "VI", p.name)
    service = Mock()
    keyboard = replace(first, kind=2, layout=0x042A042A, flags=0)
    service.catalog.return_value = InputCatalog((first, second), (keyboard,))
    service.read.return_value = InputSnapshot(
        first, api.current, "vi-VN", True, True, 0, 0, 0
    )
    service.activate.side_effect = lambda context, profile, cancelled: replace(
        service.read.return_value, profile=profile
    )
    return (
        mod.WindowsKeyboardBackend(api=api, profiles=service),
        api,
        service,
        first,
        second,
    )


def test_same_hkl_text_profiles_are_distinct_and_switch_uses_target_readback(
    text_profiles,
):
    backend, api, service, first, second = text_profiles
    state = backend.query()
    assert state.active == first.code
    assert first.code in state.available and second.code in state.available
    backend.switch(second.code, context=state.context)
    service.activate.assert_called_once_with(api.window, second, backend._stopped)
    assert backend.prepare_interaction(state).active == second.code
    assert api.requests == []


def test_profile_interaction_snapshot_uses_no_com_hooks_or_metadata_io(text_profiles):
    backend, api, service, first, _second = text_profiles
    state = backend.query()
    service.reset_mock()
    api.describe_profile = Mock(side_effect=AssertionError("GTK did metadata IO"))
    assert backend.prepare_interaction(state).active == first.code
    assert service.mock_calls == []
    api.window = mod.InputContext(500, 501, 502, 503)
    assert backend.prepare_interaction(state).active == mod._code(api.current)


@pytest.mark.parametrize(
    "reason", ["removed", "new-target", "secure", "stopped", "denied"]
)
def test_stale_or_denied_text_profile_switch_never_changes_the_cached_source(
    text_profiles, reason
):
    backend, api, service, first, second = text_profiles
    state = backend.query()
    if reason == "removed":
        service.catalog.return_value = mod.InputCatalog((first,), ())
    elif reason == "new-target":
        api.window = mod.InputContext(500, 501, 502, 503)
    elif reason == "secure":
        api.desktop_active = False
    elif reason == "stopped":
        backend.stop()
    else:
        service.activate.side_effect = PermissionError("Denied target")
    backend.switch(second.code, context=state.context)
    if reason != "denied":
        service.activate.assert_not_called()
    assert backend._profile_target[1].profile == first


def test_profile_changes_without_hkl_changes_are_refreshed_by_worker_query(
    text_profiles,
):
    from dataclasses import replace

    backend, _api, service, first, second = text_profiles
    assert backend.query().active == first.code
    service.read.return_value = replace(service.read.return_value, profile=second)
    assert backend.query().active == second.code
    service.read.side_effect = PermissionError("Target no longer permits hooks")
    assert backend.query().active.startswith("win32:")


def test_synthetic_ime_keyboard_is_hidden_until_its_plain_profile_is_enabled(
    text_profiles,
):
    from dataclasses import replace

    backend, _api, service, first, _second = text_profiles
    state = backend.query()
    assert state.active == first.code
    assert "win32:042a042a" not in state.available
    catalog = service.catalog.return_value
    keyboard = replace(catalog.keyboards[0], flags=2)
    service.catalog.return_value = replace(catalog, keyboards=(keyboard,))
    state = backend.query()
    assert keyboard.code in state.available
    backend.switch(keyboard.code, context=state.context)
    assert service.activate.call_args.args[1] == keyboard


def test_ime_compatibility_handle_missing_from_caller_catalog_is_not_selectable(
    text_profiles,
):
    from dataclasses import replace

    backend, _api, service, first, _second = text_profiles
    service.catalog.return_value = replace(service.catalog.return_value, keyboards=())
    state = backend.query()
    assert state.active == first.code
    assert "win32:042a042a" not in state.available


def test_disabling_the_last_tip_keeps_its_disabled_keyboard_hidden(text_profiles):
    from dataclasses import replace

    backend, api, service, _first, _second = text_profiles
    service.catalog.return_value = replace(service.catalog.return_value, profiles=())
    api.current = api.loaded[0]
    state = backend.query()
    assert state.active == mod._code(api.current)
    assert "win32:042a042a" not in state.available
    service.read.assert_not_called()

    # An already active source remains visible so the indicator stays truthful.
    api.current = 0x042A042A
    state = backend.query()
    assert state.active == "win32:042a042a" and state.active in state.available


def test_stale_plain_keyboard_menu_cannot_select_a_now_disabled_source(text_profiles):
    from dataclasses import replace

    backend, api, service, _first, _second = text_profiles
    disabled = service.catalog.return_value.keyboards[0]
    service.catalog.return_value = mod.InputCatalog((), (replace(disabled, flags=2),))
    api.current = api.loaded[0]
    state = backend.query()
    assert disabled.code in state.available
    service.catalog.return_value = mod.InputCatalog((), (disabled,))
    backend.switch(disabled.code, context=state.context)
    assert not api.requests
    service.activate.assert_not_called()


def test_transient_classic_layout_queries_target_language_tag_without_a_tip():
    from docking.platform.native.windows.input_thread import InputSnapshot

    api = KeyboardAPI()
    api.current = 0x04092000
    api.loaded = (api.current,)
    code = mod._code(api.current)
    api.describe = Mock(
        side_effect=lambda handle, tag="": LayoutInfo(
            code, "EN" if tag == "en-US" else "??", "English — US" if tag else "US"
        )
    )
    service = Mock()
    service.catalog.return_value = mod.InputCatalog()
    service.read.return_value = InputSnapshot(
        None, api.current, "en-US", False, False, -1, 0, 0
    )
    backend = mod.WindowsKeyboardBackend(api=api, profiles=service)
    state = backend.query()
    assert state.active == code and state.details[0].label == "EN"
    service.read.assert_called_once_with(api.window, backend._stopped)
    api.describe.assert_called_with(api.current, tag="en-US")


def test_transient_langid_is_never_resolved_as_a_stable_locale():
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    api._locale = Mock(side_effect=AssertionError("Transient LANGID was trusted"))
    for language in mod._TRANSIENT_LANGIDS:
        assert api._locale_text(language, 2) == ""


def test_layout_change_during_profile_read_retries_a_consistent_sample(text_profiles):
    from dataclasses import replace

    backend, api, service, _first, _second = text_profiles
    added = 0x04130413
    reads = 0

    def read(context, cancelled):
        nonlocal reads
        reads += 1
        if reads == 1:
            api.loaded = (*api.loaded, added)
            api.current = added
        return replace(service.read.return_value, profile=None, layout=added)

    service.read.side_effect = read
    state = backend.query()
    assert reads == 2
    assert state.active == mod._code(added)
    assert state.active in state.available
    assert added in api.described


def test_unstable_profile_read_does_not_replace_a_previously_valid_cache(text_profiles):
    from dataclasses import replace

    backend, api, service, first, _second = text_profiles
    before = backend.query()
    catalog, target, details = (
        backend._catalog,
        backend._profile_target,
        backend._details,
    )
    other = api.loaded[0]
    service.read.return_value = replace(service.read.return_value, layout=other)
    assert backend.query() == LayoutState("", [])
    assert backend._catalog is catalog
    assert backend._profile_target is target
    assert backend._details is details
    assert before.active == first.code


def test_transient_layout_aba_sample_never_applies_another_hkls_language_tag():
    from docking.platform.native.windows.input_thread import InputSnapshot

    api = KeyboardAPI()
    first, second = 0x04092000, 0x040C2400
    api.current = first
    api.loaded = (first, second)
    api.describe = Mock(
        side_effect=lambda handle, **kwargs: LayoutInfo(
            mod._code(handle), "??", "Unknown"
        )
    )
    service = Mock()
    service.catalog.return_value = mod.InputCatalog()
    # A -> B -> A: Win32 reads see A, while the target-thread observation saw B.
    service.read.return_value = InputSnapshot(
        None, second, "fr-FR", False, False, -1, 0, 0
    )
    backend = mod.WindowsKeyboardBackend(api=api, profiles=service)
    assert backend.query() == LayoutState("", [])
    assert service.read.call_count == 2
    assert all(not call.kwargs.get("tag") for call in api.describe.call_args_list)
    assert backend._details == {}


@pytest.mark.parametrize("failure", ["none", "error"])
@pytest.mark.parametrize(
    "change", ["new-app", "secure", "removed", "stopped", "destroyed"]
)
def test_failed_tsf_keyboard_activation_revalidates_win32_fallback(
    text_profiles, failure, change
):
    from dataclasses import replace

    backend, api, service, first, _second = text_profiles
    keyboard = replace(first, kind=2, layout=api.loaded[0], language=0x409, flags=2)
    service.catalog.return_value = replace(
        service.catalog.return_value, keyboards=(keyboard,)
    )
    state = backend.query()

    def failed_activation(context, profile, cancelled):
        if change == "new-app":
            api.window = mod.InputContext(999, 998, 997, 996)
        elif change == "secure":
            api.desktop_active = False
        elif change == "removed":
            api.loaded = tuple(h for h in api.loaded if h != keyboard.layout)
        elif change == "stopped":
            backend.stop()
        else:
            api.alive = False
        if failure == "error":
            raise PermissionError("Native target changed")
        return

    service.activate.side_effect = failed_activation
    backend.switch(keyboard.code, context=state.context)
    service.activate.assert_called_once()
    assert api.requests == []


def test_failed_tsf_activation_can_fall_back_for_the_still_focused_editor(
    text_profiles,
):
    from dataclasses import replace

    backend, api, service, first, _second = text_profiles
    keyboard = replace(first, kind=2, layout=api.loaded[0], language=0x409, flags=2)
    service.catalog.return_value = replace(
        service.catalog.return_value, keyboards=(keyboard,)
    )
    state = backend.query()
    service.activate.return_value = None
    service.activate.side_effect = None
    backend.switch(keyboard.code, context=state.context)
    assert api.requests == [(api.window, keyboard.layout)]
