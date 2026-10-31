"""Windows power modes: supply targeting, effective overrides and ownership."""

import ctypes
import threading
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.powerprofiles import backend, windows
from docking.applets.powerprofiles.state import PROFILES, tooltip_text
from docking.platform.backends.windows.com import GUID
from docking.platform.backends.windows.power import SystemPowerStatus


@pytest.fixture
def api(monkeypatch):
    names = [
        "GetSystemPowerStatus",
        "PowerRegisterForEffectivePowerModeNotifications",
        "PowerUnregisterFromEffectivePowerModeNotifications",
        "PowerGetActiveScheme",
        "PowerReadACValue",
        "LocalFree",
    ]
    names += [
        f"Power{op}UserConfigured{source}PowerMode"
        for source in ("AC", "DC")
        for op in ("Get", "Set")
    ]
    dll = SimpleNamespace(**{n: Mock(return_value=0) for n in names})
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: dll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    context = SimpleNamespace(
        source=1,
        values={"AC": windows._MODES["balanced"], "DC": windows._MODES["power-saver"]},
        callback=None,
        plan=GUID.parse(windows._BALANCED_PLAN),
        personality=windows._BALANCED_PLAN,
    )

    def active_plan(_root, pointer):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(GUID)))[0] = ctypes.pointer(
            context.plan
        )
        return 0

    def plan_value(_root, _active, _subgroup, _setting, kind, value, size):
        guid = GUID.parse(context.personality)
        ctypes.memmove(value, ctypes.byref(guid), ctypes.sizeof(guid))
        ctypes.cast(kind, ctypes.POINTER(ctypes.c_uint32))[0] = 3
        ctypes.cast(size, ctypes.POINTER(ctypes.c_uint32))[0] = 16
        return 0

    def status(pointer):
        ctypes.cast(
            pointer, ctypes.POINTER(SystemPowerStatus)
        ).contents.ACLineStatus = context.source
        return 1

    def register(version, callback, _context, pointer):
        assert version == 2
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = 1234
        context.callback = callback
        callback(2, None)
        return 0

    def getter(source, pointer):
        value = GUID.parse(context.values[source])
        ctypes.memmove(pointer, ctypes.byref(value), ctypes.sizeof(value))
        return 0

    def setter(source, pointer):
        value = ctypes.cast(pointer, ctypes.POINTER(GUID)).contents
        context.values[source] = str(uuid.UUID(bytes_le=bytes(value)))
        return 0

    for source in ("AC", "DC"):
        getattr(dll, f"PowerGetUserConfigured{source}PowerMode").side_effect = (
            lambda pointer, source=source: getter(source, pointer)
        )
        getattr(dll, f"PowerSetUserConfigured{source}PowerMode").side_effect = (
            lambda pointer, source=source: setter(source, pointer)
        )
    dll.GetSystemPowerStatus.side_effect = status
    dll.PowerGetActiveScheme.side_effect = active_plan
    dll.PowerReadACValue.side_effect = plan_value
    dll.PowerRegisterForEffectivePowerModeNotifications.side_effect = register
    value = windows._PowerModes()
    yield value, dll, context
    value.stop()


def test_factory_selects_windows_without_probing_unix_tools(monkeypatch):
    monkeypatch.setattr(backend.environment, "is_windows", lambda: True)
    assert isinstance(backend.detect_backend(), windows.WindowsPowerProfilesBackend)


def test_native_abi_uses_caller_owned_guid_and_shared_power_status(api):
    native, dll, _context = api
    assert ctypes.sizeof(GUID) == 16
    assert ctypes.sizeof(SystemPowerStatus) == 12
    assert dll.PowerGetUserConfiguredACPowerMode.argtypes == (ctypes.POINTER(GUID),)
    assert dll.PowerSetUserConfiguredDCPowerMode.restype is ctypes.c_uint32
    assert native.read() == windows._Snapshot("AC", windows._MODES["balanced"], 2)


@pytest.mark.parametrize("source,expected", [(1, "AC"), (0, "DC")])
def test_set_resolves_current_supply_without_rewriting_other_preference(
    api, source, expected
):
    native, dll, context = api
    original = dict(context.values)
    # Menu may have been built while the other supply was active.
    native.read()
    context.source = source
    value = windows.WindowsPowerProfilesBackend(api=native)
    assert value.set_active_profile("Perf")
    assert context.values[expected] == windows._MODES["performance"]
    other = "DC" if expected == "AC" else "AC"
    assert context.values[other] == original[other]
    getattr(dll, f"PowerSetUserConfigured{other}PowerMode").assert_not_called()
    assert value.get_state().profiles == PROFILES


def test_unknown_supply_never_guesses_a_preference_or_writes_both(api):
    native, dll, context = api
    context.source = 255
    value = windows.WindowsPowerProfilesBackend(api=native)
    assert not value.get_state().available
    assert not value.set_active_profile("performance")
    dll.PowerSetUserConfiguredACPowerMode.assert_not_called()
    dll.PowerSetUserConfiguredDCPowerMode.assert_not_called()


def test_changing_supply_during_read_is_bounded(api):
    native, dll, _context = api
    values = iter([1, 0] * 3)

    def changing(pointer):
        ctypes.cast(
            pointer, ctypes.POINTER(SystemPowerStatus)
        ).contents.ACLineStatus = next(values)
        return 1

    dll.GetSystemPowerStatus.side_effect = changing
    with pytest.raises(OSError, match="kept changing"):
        native.read()
    assert dll.GetSystemPowerStatus.call_count == 6


@pytest.mark.parametrize(
    "mode,reason",
    [
        (0, "Energy saver"),
        (1, "Power Saver"),
        (2, ""),
        (3, "Performance"),
        (4, "Performance"),
        (5, "Game Mode"),
        (6, "Mixed Reality"),
        (None, "unavailable"),
        (100, "unavailable"),
    ],
)
def test_effective_override_never_replaces_configured_menu_choice(api, mode, reason):
    native, _dll, context = api
    if mode is None:
        native._effective = None
    else:
        context.callback(mode, None)
    value = windows.WindowsPowerProfilesBackend(api=native)
    state = value.get_state()
    assert state.available and state.active_profile == "balanced"
    if reason:
        assert reason in state.degraded_reason
    else:
        assert state.degraded_reason == ""
    assert "Power source: Plugged in" in tooltip_text(state)


def test_unknown_configured_guid_does_not_fabricate_balanced_or_selectable_state(api):
    native, _dll, context = api
    context.values["AC"] = "01234567-89ab-cdef-0123-456789abcdef"
    state = windows.WindowsPowerProfilesBackend(api=native).get_state()
    assert not state.available and not state.active_profile


def test_custom_balanced_plan_is_supported_but_other_personalities_are_not_replaced(
    api,
):
    native, dll, context = api
    context.plan = GUID.parse("01234567-89ab-cdef-0123-456789abcdef")
    value = windows.WindowsPowerProfilesBackend(api=native)
    assert value.get_state().available
    context.personality = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
    state = value.get_state()
    assert not state.available and "Balanced power plan" in state.error
    assert not value.set_active_profile("performance")
    dll.PowerSetUserConfiguredACPowerMode.assert_not_called()
    context.personality = windows._BALANCED_PLAN
    assert value.get_state().available


def test_allocated_plan_guid_is_freed_even_if_personality_query_fails(api):
    native, dll, _context = api
    dll.PowerReadACValue.side_effect = None
    dll.PowerReadACValue.return_value = 5
    with pytest.raises(OSError):
        native.plan()
    dll.LocalFree.assert_called_once()


def test_native_denial_and_success_without_readback_do_not_optimistically_select(api):
    native, dll, context = api
    value = windows.WindowsPowerProfilesBackend(api=native)
    dll.PowerSetUserConfiguredACPowerMode.side_effect = None
    dll.PowerSetUserConfiguredACPowerMode.return_value = 5
    assert not value.set_active_profile("performance")
    dll.PowerSetUserConfiguredACPowerMode.return_value = 0
    assert not value.set_active_profile("performance")
    assert context.values["AC"] == windows._MODES["balanced"]
    assert value.get_state().active_profile == "balanced"
    assert not value.set_active_profile("invalid")


def test_unregister_waits_without_holding_callback_lock_and_late_delivery_is_ignored(
    api,
):
    native, dll, context = api
    callback = context.callback
    dll.PowerUnregisterFromEffectivePowerModeNotifications.side_effect = lambda _h: (
        callback(4, None) or 0
    )
    worker = threading.Thread(target=native.stop, daemon=True)
    worker.start()
    worker.join(2)
    assert not worker.is_alive(), "Unregister deadlocked its callback"
    assert not native._registration.value and native._callback is None
    assert native._effective == 2
    callback(0, None)
    assert native._effective == 2
    native.stop()
    dll.PowerUnregisterFromEffectivePowerModeNotifications.assert_called_once()


def test_failed_unregistration_keeps_native_trampoline_alive(api, monkeypatch):
    native, dll, _context = api
    retained = []
    monkeypatch.setattr(windows, "_retained_registrations", retained)
    dll.PowerUnregisterFromEffectivePowerModeNotifications.return_value = -1
    native.stop()
    assert retained == [native] and native._callback is not None


def test_stopped_backend_never_reopens_native_registration_or_writes(api):
    native, dll, _context = api
    value = windows.WindowsPowerProfilesBackend(api=native)
    value.stop()
    assert not value.get_state().available
    assert not value.set_active_profile("performance")
    assert dll.PowerRegisterForEffectivePowerModeNotifications.call_count == 1
    dll.PowerSetUserConfiguredACPowerMode.assert_not_called()


def test_stop_waits_for_inflight_native_write_before_releasing_subscription():
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

    def set_mode(_guid):
        entered.set()
        assert release.wait(3)
        return True

    api = SimpleNamespace(set=set_mode, stop=stopped.set)
    value = windows.WindowsPowerProfilesBackend(api=api)
    writer = threading.Thread(target=lambda: value.set_active_profile("performance"))
    writer.start()
    assert entered.wait(1)
    closer = threading.Thread(target=value.stop)
    closer.start()
    assert not stopped.wait(0.1)
    release.set()
    writer.join(2)
    closer.join(2)
    assert not writer.is_alive() and not closer.is_alive() and stopped.is_set()


def test_native_subscription_cycles_release_handles_without_changing_modes():
    import sys

    if sys.platform != "win32":
        pytest.skip("Native Windows power modes")
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

    warm = windows._PowerModes()
    before_modes = warm.configured("AC"), warm.configured("DC")
    warm.stop()
    baseline = handles()
    for _ in range(20):
        value = windows._PowerModes()
        try:
            assert (value.configured("AC"), value.configured("DC")) == before_modes
            assert value.read().source in ("AC", "DC")
        finally:
            value.stop()
        assert value._callback is None and not value._registration.value
    assert handles() <= baseline + 1
