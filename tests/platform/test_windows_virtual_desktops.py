"""COM ownership and window membership use explicit native ABI contracts."""

import ctypes
import os
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.platform.backends.base import ActionResult
from docking.platform.backends.windows import virtual_desktops as module

FIRST = "00000000-0000-0000-0000-000000000001"
SECOND = "00000000-0000-0000-0000-000000000002"


@pytest.mark.parametrize(
    "build,expected", [(26100, True), (26200, True), (26000, False), (26300, False)]
)
def test_shell_abi_only_runs_on_identified_windows_builds(monkeypatch, build, expected):
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(
        module.sys,
        "getwindowsversion",
        lambda: SimpleNamespace(build=build),
        raising=False,
    )
    assert module.WindowsVirtualDesktopAPI.supported() is expected


def test_other_platforms_do_not_query_windows_version_or_load_libraries(monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "linux")
    dll = Mock(side_effect=AssertionError("Must remain lazy"))
    monkeypatch.setattr(ctypes, "WinDLL", dll, raising=False)
    api = module.WindowsVirtualDesktopAPI()
    assert not api.supported()
    with pytest.raises(OSError, match="unavailable"):
        api.read()
    dll.assert_not_called()


@pytest.mark.parametrize("status,pointer", [(0, 77), (-1, 77), (0, None), (-1, None)])
def test_partial_com_results_are_released_once_even_on_failed_hresult(
    monkeypatch, status, pointer
):
    released = []
    monkeypatch.setattr(module, "release", lambda p: released.append(p.value))

    def query(this, output):
        assert this.value == 1
        output._obj.value = pointer
        return status

    monkeypatch.setattr(module, "method", lambda p, slot, result, *types: query)
    with ExitStack() as stack:
        if status < 0 or pointer is None:
            with pytest.raises(OSError):
                module._object(stack, ctypes.c_void_p(1), 7)
        else:
            assert module._object(stack, ctypes.c_void_p(1), 7).value == pointer
        assert not released
    assert released == ([77] if pointer else [])


@pytest.mark.parametrize("status", [0, -1])
def test_cocreate_partial_pointer_is_owned_before_hresult_is_checked(
    monkeypatch, status
):
    released = []
    monkeypatch.setattr(module, "release", lambda p: released.append(p.value))

    def create(cls, outer, context, iid, output):
        assert outer is None and context == 4
        output._obj.value = 42
        return status

    with ExitStack() as stack:
        if status < 0:
            with pytest.raises(OSError):
                module.WindowsVirtualDesktopAPI._create(
                    stack, create, module._SHELL, module._PROVIDER, 4
                )
        else:
            assert (
                module.WindowsVirtualDesktopAPI._create(
                    stack, create, module._SHELL, module._PROVIDER, 4
                ).value
                == 42
            )
    assert released == [42]


@pytest.mark.parametrize("name_size,status", [(4, 0), (0, 0), (5000, 0), (4, -1)])
def test_hstring_is_released_on_success_and_native_failure(
    monkeypatch, name_size, status
):
    library = Mock()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args: library, raising=False)
    text = ctypes.create_unicode_buffer("Code")
    library.WindowsGetStringRawBuffer.side_effect = lambda value, output: (
        setattr(output._obj, "value", name_size) or ctypes.addressof(text)
    )
    library.WindowsDeleteString.return_value = 0

    def get(this, output):
        output._obj.value = 99
        return status

    monkeypatch.setattr(module, "method", lambda *_args: get)
    if status < 0 or name_size > 4096:
        with pytest.raises(OSError):
            module.WindowsVirtualDesktopAPI._name(ctypes.c_void_p(1))
    else:
        assert module.WindowsVirtualDesktopAPI._name(ctypes.c_void_p(1)) == (
            "Code" if name_size else ""
        )
    assert library.WindowsDeleteString.call_args.args[0].value == 99


def test_public_membership_preserves_unknown_pinned_and_other_desktop_states(
    monkeypatch,
):
    # Wide HWNDs and a real three-argument callable catch missing COM `this`
    # arguments and accidental 32-bit truncation at this boundary.
    handles = [0x100000001 + i for i in range(6)]
    native = Mock()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kw: native, raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    native.EnumWindows.side_effect = lambda visit, data: all(
        visit(h, data) for h in handles
    )
    native.IsWindowVisible.side_effect = lambda h: h != handles[4]
    visits = {}

    def owner(hwnd, output):
        visits[hwnd] = visits.get(hwnd, 0) + 1
        output._obj.value = (
            os.getpid()
            if hwnd == handles[5]
            else 56
            if hwnd == handles[3] and visits[hwnd] > 1
            else 55
        )
        return 1

    native.GetWindowThreadProcessId.side_effect = owner

    def current(this, hwnd, output):
        assert this.value == 90 and hwnd in handles
        output._obj.value = int(hwnd != handles[0])
        return -1 if hwnd == handles[2] else 0

    def identifier(this, hwnd, output):
        assert this.value == 90
        if hwnd != handles[2]:
            ctypes.memmove(
                ctypes.byref(output._obj),
                ctypes.byref(
                    module.GUID.parse(SECOND if hwnd == handles[1] else FIRST)
                ),
                ctypes.sizeof(module.GUID),
            )
        return -1 if hwnd == handles[2] else 0

    def method(pointer, index, result, *types):
        assert pointer.value == 90 and len(types) == 2
        return {3: current, 4: identifier}[index]

    monkeypatch.setattr(module, "method", method)
    rows = module.WindowsVirtualDesktopAPI._windows(
        ctypes.c_void_p(90), frozenset({FIRST})
    )
    assert rows == {
        handles[0]: module.WindowWorkspace(55, FIRST, False),
        handles[1]: module.WindowWorkspace(55, SECOND, True, sticky=True),
        handles[2]: module.WindowWorkspace(55, None, None),
    }
    assert native.EnumWindows.argtypes[1] is ctypes.c_ssize_t


def switching_api(monkeypatch, current=FIRST):
    api = module.WindowsVirtualDesktopAPI()
    manager = ctypes.c_void_p(99)

    @contextmanager
    def session():
        with ExitStack() as stack:
            yield stack, manager, None

    monkeypatch.setattr(api, "_session", session)
    monkeypatch.setattr(
        api, "_desktops", lambda *_args: (ctypes.c_void_p(1), ctypes.c_void_p(2))
    )
    monkeypatch.setattr(
        api, "_id", lambda p: {1: FIRST, 2: SECOND, 3: current}[p.value]
    )
    monkeypatch.setattr(module, "_object", lambda stack, p, slot: ctypes.c_void_p(3))
    switch = Mock(return_value=0)
    method = Mock(return_value=switch)
    monkeypatch.setattr(module, "method", method)
    return api, switch, method


@pytest.mark.parametrize(
    "target,cancelled,expected,invokes",
    [
        (SECOND, False, ActionResult.OK, 1),
        (FIRST, False, ActionResult.OK, 0),
        (SECOND, True, ActionResult.NOT_FOUND, 0),
        ("missing", False, ActionResult.NOT_FOUND, 0),
    ],
)
def test_switch_resolves_fresh_target_and_uses_identified_slot_only(
    monkeypatch, target, cancelled, expected, invokes
):
    api, switch, method = switching_api(monkeypatch)
    assert api.activate(target, lambda: cancelled) is expected
    assert switch.call_count == invokes
    if invokes:
        assert method.call_args.args[1:] == (9, module.HRESULT, ctypes.c_void_p)
        assert [p.value for p in switch.call_args.args] == [99, 2]


@pytest.mark.parametrize(
    "failure", [None, "duplicate", "missing-active", "active-changed"]
)
def test_snapshot_requires_one_consistent_active_desktop_and_keeps_membership_immutable(
    monkeypatch, failure
):
    api, switch, _method = switching_api(monkeypatch)
    identifiers = {1: FIRST, 2: FIRST if failure == "duplicate" else SECOND}
    current_reads = []

    def identifier(pointer):
        if pointer.value != 3:
            return identifiers[pointer.value]
        current_reads.append(True)
        if failure == "missing-active":
            return "missing"
        return (
            SECOND if failure == "active-changed" and len(current_reads) > 1 else FIRST
        )

    monkeypatch.setattr(api, "_id", identifier)
    monkeypatch.setattr(api, "_name", lambda p: {1: "First", 2: "Second"}[p.value])
    monkeypatch.setattr(api, "_create", lambda *_args: ctypes.c_void_p(90))
    membership = {100: module.WindowWorkspace(55, FIRST, True)}
    monkeypatch.setattr(api, "_windows", lambda public, ids: membership)
    if failure:
        with pytest.raises(OSError, match="changed"):
            api.read()
    else:
        state = api.read()
        assert [row.active for row in state.desktops] == [True, False]
        assert [row.number for row in state.desktops] == [0, 1]
        assert state.windows[100] == membership[100]
        with pytest.raises(TypeError):
            state.windows[101] = membership[100]
    switch.assert_not_called()


@pytest.mark.parametrize(
    "initial,wanted,slot",
    [(False, True, 7), (True, False, 8), (True, True, None), (False, False, None)],
)
def test_surface_stickiness_pins_only_one_owned_view_and_is_idempotent(
    monkeypatch, initial, wanted, slot
):
    api = module.WindowsVirtualDesktopAPI()
    monkeypatch.setattr(api, "supported", lambda: True)
    user = Mock()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: user, raising=False)
    user.GetWindowThreadProcessId.side_effect = lambda hwnd, output: (
        setattr(output._obj, "value", os.getpid()) or 1
    )

    @contextmanager
    def shell():
        with ExitStack() as stack:
            yield stack, ctypes.c_void_p(1), None

    monkeypatch.setattr(api, "_shell", shell)

    def query(stack, pointer, index, *arguments):
        if pointer.value == 1:
            assert index == 3
            return ctypes.c_void_p(
                2 if bytes(arguments[1]._obj) == bytes(module._VIEWS) else 4
            )
        assert pointer.value == 2 and index == 6
        assert arguments == (ctypes.c_void_p, 0x100000001)
        return ctypes.c_void_p(3)

    monkeypatch.setattr(module, "_object", query)
    changed = []

    def method(pointer, index, result, *arguments):
        assert pointer.value == 4
        if index == 6:

            def read(this, view, output):
                assert view.value == 3
                output._obj.value = initial
                return 0

            return read
        assert index in (7, 8)
        return lambda this, view: changed.append(index) or 0

    monkeypatch.setattr(module, "method", method)
    api.set_pinned(0x100000001, wanted)
    assert changed == ([] if slot is None else [slot])


def test_foreign_or_reused_surface_cannot_be_pinned(monkeypatch):
    api = module.WindowsVirtualDesktopAPI()
    monkeypatch.setattr(api, "supported", lambda: True)
    user = Mock()
    user.GetWindowThreadProcessId.side_effect = lambda hwnd, output: (
        setattr(output._obj, "value", os.getpid() + 1) or 1
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: user, raising=False)
    shell = Mock()
    monkeypatch.setattr(api, "_shell", shell)
    with pytest.raises(OSError, match="no longer owned"):
        api.set_pinned(123, True)
    shell.assert_not_called()
