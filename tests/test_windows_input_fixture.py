"""Owned IME setup must restore mode, preserve identity and bound foreign sends."""

import ctypes
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from unittest.mock import Mock

import pytest

from tools.windows.visual import input_fixture as fixture


class ModeAPI:
    def __init__(self):
        self.local = True
        self.original = None
        self.mode = fixture.IMEMode(False, 1, 2)
        self.alive = True
        self.calls = []
        self.threads = set()

    def identity(self):
        self.threads.add(threading.get_ident())
        if not self.alive:
            raise RuntimeError("Owned context changed")
        return {"window": 10, "root": 10, "pid": 20, "thread": 30, "ime_window": 40}

    def read(self):
        self.threads.add(threading.get_ident())
        return self.mode

    def set_mode(self, *, opened, conversion, sentence):
        self.identity()
        self.calls.append((opened, conversion, sentence))
        # Opening can implicitly change both conversion and sentence state.
        # Setup must preserve the pre-open snapshot for exact restoration.
        self.mode = fixture.IMEMode(
            opened,
            9 if conversion is None else conversion,
            8 if conversion is None else sentence,
        )


@pytest.fixture
def mode_api(monkeypatch):
    native = ModeAPI()
    monkeypatch.setattr(fixture, "_IME", lambda *args: native)
    return native


def scope(journal, **kwargs):
    return fixture.ime_conversion(
        window=10, root=10, pid=20, thread=30, journal=journal, **kwargs
    )


@pytest.mark.parametrize("body_fails", [False, True])
def test_open_closed_mode_without_forcing_conversion_and_restore_implicit_changes(
    mode_api, body_fails
):
    journal = {}
    original = mode_api.mode

    def exercise():
        with scope(journal):
            assert mode_api.mode == fixture.IMEMode(True, 9, 8)
            assert journal["before"] == asdict(original)
            assert mode_api.calls == [(True, None, 2)]
            if body_fails:
                raise ValueError("Composition failed")

    if body_fails:
        with pytest.raises(ValueError, match="Composition failed"):
            exercise()
    else:
        exercise()
    assert mode_api.mode == original
    assert journal["restored"]
    assert mode_api.calls[-1] == (False, 1, 2)


def test_already_open_mode_is_preserved_and_body_mode_changes_are_restored(mode_api):
    mode_api.mode = fixture.IMEMode(True, 9, 8)
    journal = {}
    with scope(journal):
        assert not mode_api.calls
        mode_api.mode = fixture.IMEMode(True, 17, 3)
    assert mode_api.calls == [(True, 9, 8)]
    assert journal["restore_attempted"] and journal["restored"]


def test_explicit_conversion_request_is_verified_then_restored(mode_api):
    journal = {}
    with scope(journal, conversion=17):
        assert mode_api.mode == fixture.IMEMode(True, 17, 2)
    assert mode_api.mode == fixture.IMEMode(False, 1, 2)
    assert journal["restored"]


def test_unsupported_open_fails_before_composition_without_silent_fallback(
    mode_api, monkeypatch
):
    monkeypatch.setattr(mode_api, "set_mode", lambda **kwargs: None)
    journal = {}
    with pytest.raises(RuntimeError, match="did not enter"), scope(journal):
        pytest.fail("Closed IME must not enter the composition fixture")
    assert journal["restored"]


def test_partial_timeout_still_restores_prior_open_conversion_and_sentence(
    mode_api, monkeypatch
):
    set_mode = mode_api.set_mode

    def timed_out(**kwargs):
        set_mode(**kwargs)
        if kwargs["opened"]:
            raise TimeoutError("Foreign setter timed out after changing mode")

    monkeypatch.setattr(mode_api, "set_mode", timed_out)
    journal = {}
    with pytest.raises(TimeoutError), scope(journal):
        pytest.fail("Timed-out setup must fail")
    assert mode_api.mode == fixture.IMEMode(False, 1, 2)
    assert journal["error"] == "TimeoutError" and journal["restored"]


def test_restore_mismatch_is_reported_and_propagated(mode_api, monkeypatch):
    set_mode = mode_api.set_mode

    def refuse_restore(**kwargs):
        if kwargs["opened"]:
            set_mode(**kwargs)

    monkeypatch.setattr(mode_api, "set_mode", refuse_restore)
    journal = {}
    with pytest.raises(RuntimeError, match="restore exactly"), scope(journal):
        pass
    assert not journal["restored"] and journal["restore_error"] == "RuntimeError"


def test_lost_target_identity_never_mutates_replacement_on_cleanup(mode_api):
    journal = {}
    with pytest.raises(RuntimeError, match="context changed"), scope(journal):
        mode_api.alive = False
    assert mode_api.calls == [(True, None, 2)]
    assert not journal["restored"] and "restore_error" in journal


def test_local_setup_and_restore_execute_on_one_dispatched_ui_thread(mode_api):
    journal = {}
    controller_thread = threading.get_ident()
    validations = []

    def validate():
        validations.append(threading.get_ident())

    with ThreadPoolExecutor(max_workers=1) as ui:
        ui_thread = ui.submit(threading.get_ident).result()

        def dispatch(operation):
            return ui.submit(operation).result(timeout=2)

        with scope(journal, dispatch=dispatch, validate=validate):
            assert threading.get_ident() != ui_thread
    assert mode_api.threads == {ui_thread}
    assert validations and set(validations) == {controller_thread}
    assert journal["restored"]


def test_same_hkl_profile_change_prevents_restoring_mode_to_wrong_profile(mode_api):
    profile = ["telex"]
    journal = {}
    original_identity = mode_api.identity()

    def validate():
        if profile[0] != "telex":
            raise RuntimeError("Owned IME profile changed")

    with (
        pytest.raises(RuntimeError, match="profile changed"),
        scope(journal, validate=validate),
    ):
        profile[0] = "vni"
        assert mode_api.identity() == original_identity
    assert mode_api.calls == [(True, None, 2)]
    assert not journal["restored"] and journal["restore_error"] == "RuntimeError"


def test_profile_change_after_initial_read_blocks_setup_mutation(mode_api, monkeypatch):
    profile = ["telex"]
    read = mode_api.read

    def changed_read():
        before = read()
        profile[0] = "vni"
        return before

    def validate():
        if profile[0] != "telex":
            raise RuntimeError("Owned IME profile changed")

    monkeypatch.setattr(mode_api, "read", changed_read)
    with (
        pytest.raises(RuntimeError, match="profile changed"),
        scope({}, validate=validate),
    ):
        pytest.fail("Changed TSF profile must not enter setup")
    assert not mode_api.calls


def test_foreign_setup_requires_and_uses_independent_input_thread_readback(
    mode_api, monkeypatch
):
    mode_api.local = False
    with pytest.raises(ValueError, match="independent"), scope({}):
        pytest.fail("Foreign context requires independent readback")
    assert not mode_api.calls
    monkeypatch.setattr(
        mode_api, "read", Mock(side_effect=AssertionError("Foreign IMM direct read"))
    )
    readback = Mock(side_effect=lambda: mode_api.mode)
    journal = {}
    with scope(journal, readback=readback):
        assert journal["transport"] == "foreign-legacy-ime-control"
    assert readback.call_count >= 4 and journal["restored"]


def test_unknown_foreign_snapshot_fails_without_any_setter(mode_api):
    mode_api.local = False

    def unknown():
        raise RuntimeError("Unknown target input-thread state")

    with pytest.raises(RuntimeError, match="Unknown"), scope({}, readback=unknown):
        pytest.fail("Unknown mode must not be overwritten")
    assert not mode_api.calls


def test_foreign_control_uses_only_owned_ime_window_scalar_bounded_message(monkeypatch):
    native = object.__new__(fixture._IME)
    native.user = "user"
    native.identity = lambda: {"ime_window": 0x12345678}
    sends = []

    def send(window, message, first, second, flags, timeout, result):
        sends.append((window, message, first, second, flags, timeout))
        result._obj.value = 0  # Message result zero is distinct from send failure.
        return 1

    def bind(library, name, restype, *argtypes):
        assert name == "SendMessageTimeoutW" and library == "user"
        assert restype is ctypes.c_ssize_t
        assert argtypes[2:4] == (ctypes.c_size_t, ctypes.c_ssize_t)
        assert argtypes[-1] == ctypes.POINTER(ctypes.c_size_t)
        return send

    monkeypatch.setattr(fixture, "bind", bind)
    monkeypatch.setattr(fixture.c, "set_last_error", lambda value: None, raising=False)
    native._control(6, 1)
    assert sends == [(0x12345678, 0x283, 6, 1, 0x23, 1000)]


def test_foreign_send_failure_is_not_a_successful_zero_result(monkeypatch):
    native = object.__new__(fixture._IME)
    native.user = "user"
    native.identity = lambda: {"ime_window": 10}
    monkeypatch.setattr(fixture, "bind", lambda *args: lambda *values: 0)
    monkeypatch.setattr(fixture.c, "set_last_error", lambda value: None, raising=False)
    monkeypatch.setattr(fixture.c, "get_last_error", lambda: 1460, raising=False)
    with pytest.raises(TimeoutError, match="1460"):
        native._control(6, 1)


@pytest.fixture
def local_imm(monkeypatch):
    native = object.__new__(fixture._IME)
    native.window, native.root, native.pid, native.thread = 11, 10, 12, 30
    native.local = True
    native.user, native.kernel, native.imm = "user", "kernel", "imm"
    native.original = native.context = None
    state = {
        "owners": {10: (30, 12), 11: (30, 12), 40: (30, 12)},
        "foreground": 10,
        "focus": 11,
        "thread": 30,
        "layout": 0x042A042A,
        "context": 99,
        "mode": fixture.IMEMode(False, 1, 2),
        "setters": [],
        "released": [],
    }

    def owner(window, pid):
        thread, process = state["owners"].get(window, (0, 0))
        pid._obj.value = process
        return thread

    def gui(thread, info):
        assert thread == 30
        info._obj.focus = state["focus"]
        return 1

    def conversion(context, mode, sentence):
        mode._obj.value = state["mode"].conversion
        sentence._obj.value = state["mode"].sentence
        return 1

    def opened(context, value):
        state["setters"].append(("open", value))
        state["mode"] = fixture.IMEMode(value, 9, 8)
        return 1

    def convert(context, mode, sentence):
        state["setters"].append(("conversion", mode, sentence))
        state["mode"] = fixture.IMEMode(state["mode"].open, mode, sentence)
        return 1

    functions = {
        "GetWindowThreadProcessId": owner,
        "IsChild": lambda root, window: (root, window) == (10, 11),
        "GetForegroundWindow": lambda: state["foreground"],
        "GetGUIThreadInfo": gui,
        "GetCurrentThreadId": lambda: state["thread"],
        "ImmGetDefaultIMEWnd": lambda window: 40,
        "GetKeyboardLayout": lambda thread: state["layout"],
        "ImmGetContext": lambda window: state["context"],
        "ImmReleaseContext": lambda window, context: (
            state["released"].append((window, context)) or 1
        ),
        "ImmGetOpenStatus": lambda context: state["mode"].open,
        "ImmGetConversionStatus": conversion,
        "ImmSetOpenStatus": opened,
        "ImmSetConversionStatus": convert,
    }
    monkeypatch.setattr(fixture, "bind", lambda library, name, *types: functions[name])
    return native, state


def test_local_imm_uses_current_context_and_releases_every_acquisition(local_imm):
    native, state = local_imm
    native.original = native.identity()
    assert native.read() == fixture.IMEMode(False, 1, 2)
    native.set_mode(opened=True, conversion=None, sentence=2)
    assert native.read() == fixture.IMEMode(True, 9, 8)
    assert state["setters"] == [("open", True)]
    native.set_mode(opened=False, conversion=1, sentence=2)
    assert native.read() == fixture.IMEMode(False, 1, 2)
    assert state["released"] == [(11, 99)] * 5


@pytest.mark.parametrize(
    "change", ["window_owner", "ime_owner", "foreground", "focus", "layout", "thread"]
)
def test_native_identity_rejects_changed_target_before_local_setters(local_imm, change):
    native, state = local_imm
    native.original = native.identity()
    if change == "window_owner":
        state["owners"][11] = (30, 13)
    elif change == "ime_owner":
        state["owners"][40] = (30, 13)
    else:
        state[change] += 1
    with pytest.raises(RuntimeError):
        native.set_mode(opened=True, conversion=None, sentence=2)
    assert not state["setters"] and not state["released"]


def test_replaced_local_input_context_is_released_without_mutating_it(local_imm):
    native, state = local_imm
    native.read()
    state["context"] = 100
    with pytest.raises(RuntimeError, match="context was replaced"):
        native.set_mode(opened=True, conversion=None, sentence=2)
    assert state["released"] == [(11, 99), (11, 100)]
    assert not state["setters"]


def test_failed_local_open_releases_its_input_context(local_imm, monkeypatch):
    native, state = local_imm
    bind = fixture.bind
    monkeypatch.setattr(
        fixture,
        "bind",
        lambda library, name, *types: (
            (lambda *args: 0)
            if name == "ImmSetOpenStatus"
            else bind(library, name, *types)
        ),
    )
    with pytest.raises(RuntimeError, match="open status change failed"):
        native.set_mode(opened=True, conversion=None, sentence=2)
    assert state["released"] == [(11, 99)]


def test_fixture_import_preserves_installed_package_selection_boundary():
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import tools.windows.visual.input_fixture; "
                "assert not any(n == 'docking' or n.startswith('docking.') "
                "for n in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
