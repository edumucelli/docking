"""Windows input-locale identity, foreground ownership and switch readback."""

import ctypes
import sys
import time
from ctypes import wintypes

import pytest

from docking.applets.keyboardlayout import windows as mod
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

    def describe(self, handle):
        self.described.append(handle)
        label, name = {
            self.loaded[0]: ("EN", "US"),
            self.loaded[1]: ("EN", "US-Dvorak"),
            self.loaded[2]: ("FR", "French"),
        }.get(handle, ("??", "Unknown"))
        return LayoutInfo(mod._code(handle), label, name)

    def request(self, context, handle):
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


def test_layout_request_is_posted_to_verified_focused_input_window():
    api = mod.WindowsKeyboardAPI.__new__(mod.WindowsKeyboardAPI)
    calls = []
    api._post = lambda *args: calls.append(args) or True
    context = mod.InputContext(100, 101, 201, 301)
    assert api.request(context, 0x040C040C)
    assert calls == [(101, 0x50, 0, 0x040C040C)]
    calls.clear()
    context = mod.InputContext(100, 101, 201, 301, root_thread=200, root_pid=301)
    assert api.request(context, 0x040C040C)
    assert calls == [(101, 0x50, 0, 0x040C040C)]


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


@pytest.fixture
def native_keyboard_window():
    """Own a Win32 input window without measuring unrelated GTK initialization."""
    from docking.platform.backends.windows.ffi import bind

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
