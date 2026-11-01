"""Windows battery ABI, reported-state semantics and shared UI regressions."""

import ctypes
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.battery import render, state
from docking.applets.battery.implementations.unix import upower as upower
from docking.applets.battery.implementations.windows import backend as windows
from docking.applets.battery.state import BatteryState
from docking.platform.composition.windows import WindowsAppletBackendFactories
from tests.platform_factories import TestAppletBackendFactories, make_applet_platform


def _backend(*, power=None, battery=None):
    api = SimpleNamespace(
        power_status=Mock(return_value=power),
        battery_state=Mock(return_value=battery),
        peripheral_batteries=Mock(return_value=[]),
    )
    return windows.WindowsBatteryBackend(api=api), api


def _status(**values):
    return windows.SystemPowerStatus(
        **{
            "ACLineStatus": 0,
            "BatteryFlag": 1,
            "BatteryLifePercent": 75,
            "BatteryLifeTime": 7200,
            "BatteryFullLifeTime": 9600,
            **values,
        }
    )


@pytest.mark.parametrize(
    "ac,flags,percent,status,seconds",
    [
        (0, 1, 75, "Discharging", 7200),
        (1, 9, 75, "Charging", None),
        (1, 1, 75, "Not charging", None),
        (1, 1, 100, "Full", None),
        (0, 0, 0, "Discharging", 7200),
        (255, 8, 75, "Charging", None),
        (255, 0, 75, "Unknown", None),
        (1, 255, 75, "Unknown", None),
    ],
)
def test_charge_direction_comes_from_reported_flags_not_ac_alone(
    ac, flags, percent, status, seconds
):
    value, _api = _backend(
        power=_status(ACLineStatus=ac, BatteryFlag=flags, BatteryLifePercent=percent)
    )
    reading = value.read_battery()
    assert reading is not None
    assert (reading.capacity, reading.status, reading.seconds_remaining) == (
        percent,
        status,
        seconds,
    )
    assert reading.icon_name.endswith("-charging") == (status in {"Charging", "Full"})


@pytest.mark.parametrize("percent", [101, 254, 255])
def test_unknown_percentage_never_becomes_zero_or_a_fabricated_percent(percent):
    value, _api = _backend(power=_status(BatteryLifePercent=percent))
    reading = value.read_battery()
    assert reading is not None and reading.capacity is None
    assert state.tooltip_text(reading).startswith("Battery: Unknown")


def test_unknown_flags_are_not_interpreted_as_no_battery_or_charging():
    value, _api = _backend(power=_status(BatteryFlag=255, BatteryLifePercent=255))
    assert value.read_battery() == BatteryState("battery-empty", None, "Unknown", None)


@pytest.mark.parametrize("flags", [128, 129])
def test_explicit_absent_battery_has_no_state_or_rate_query(flags):
    value, api = _backend(power=_status(BatteryFlag=flags, BatteryLifePercent=255))
    assert value.read_battery() is None
    api.battery_state.assert_not_called()


@pytest.mark.parametrize("seconds", [0, 0xFFFFFFFF])
def test_unknown_or_zero_estimate_is_omitted(seconds):
    value, _api = _backend(power=_status(BatteryLifeTime=seconds))
    assert value.read_battery().seconds_remaining is None


@pytest.mark.parametrize("rate,expected", [(-7600, 7.6), (12500, 12.5), (0, 0.0)])
def test_signed_milliwatts_become_power_magnitude_in_watts(rate, expected):
    value, _api = _backend(
        power=_status(), battery=windows.SystemBatteryState(BatteryPresent=1, Rate=rate)
    )
    assert value.read_power_watts() == expected
    assert value.read_battery().power_watts == expected


@pytest.mark.parametrize(
    "battery",
    [
        None,
        windows.SystemBatteryState(BatteryPresent=0, Rate=7600),
        windows.SystemBatteryState(BatteryPresent=1, Rate=-0x80000000),
    ],
)
def test_missing_or_unknown_rate_is_not_reported_as_power(battery):
    value, _api = _backend(power=_status(), battery=battery)
    reading = value.read_battery()
    assert reading.capacity == 75 and reading.power_watts is None


def test_failed_read_recovers_on_next_poll_without_caching_absence():
    value, api = _backend()
    unknown = value.read_battery()
    assert unknown.capacity is None and unknown.status == "Unknown"
    api.power_status.return_value = _status(BatteryLifePercent=55)
    assert value.read_battery().capacity == 55
    api.power_status.return_value = _status(BatteryFlag=128)
    assert value.read_battery() is None


def test_unknown_capacity_has_a_distinct_glyph_from_zero_and_absence():
    unknown = BatteryState("battery-empty", None, "Unknown", None)
    empty = unknown._replace(capacity=0)
    for overlay in (state.OVERLAY_NONE, state.OVERLAY_PERCENT, state.OVERLAY_POWER):
        pixels = render.render_icon(48, unknown, overlay).get_pixels()
        assert pixels != render.render_icon(48, empty, overlay).get_pixels()
        assert pixels != render.render_icon(48, None, overlay).get_pixels()


def test_factory_selects_windows_without_linux_specific_arguments(monkeypatch):
    assert isinstance(
        WindowsAppletBackendFactories().battery(), windows.WindowsBatteryBackend
    )


def test_windows_settings_uses_native_uri_association(monkeypatch):
    from docking.platform.native.windows import shell

    launch = Mock(return_value=True)
    monkeypatch.setattr(shell, "open_default_target", launch)
    value = WindowsAppletBackendFactories().battery()
    assert value.settings_available()
    assert value.open_settings()
    launch.assert_called_once_with("ms-settings:powersleep")
    launch.return_value = False
    assert not value.open_settings()


def test_windows_does_not_attempt_upower_peripheral_discovery(monkeypatch):
    bus = Mock()
    monkeypatch.setattr(upower.Gio, "bus_get_sync", bus)
    _value, api = _backend()
    monkeypatch.setattr(windows, "_native_api", lambda: api)
    assert WindowsAppletBackendFactories().battery().read_peripherals() == []
    api.peripheral_batteries.assert_called_once_with()
    bus.assert_not_called()


@pytest.mark.parametrize("error", [OSError("unavailable"), ValueError("bad response")])
def test_peripheral_query_failure_recovers_on_the_next_poll(error):
    from docking.applets.battery.peripherals import PeripheralBattery

    value, api = _backend()
    peripheral = PeripheralBattery("Fixture keyboard", "Keyboard", 0, False, "")
    api.peripheral_batteries.side_effect = [error, [peripheral]]
    assert value.read_peripherals() == []
    assert value.read_peripherals() == [peripheral]


@pytest.mark.parametrize("lifecycle", ["active", "stopped", "replaced"])
def test_peripheral_queries_run_off_gtk_and_late_replies_cannot_repaint(
    monkeypatch, lifecycle
):
    import threading
    from queue import Queue

    from docking.applets.battery import applet as ui
    from docking.applets.battery.peripherals import PeripheralBattery
    from docking.applets.worker import BackgroundWorker
    from docking.core.config import Config

    value, api = _backend()
    monkeypatch.setattr(
        TestAppletBackendFactories, "battery", staticmethod(lambda: value)
    )
    entered, release = threading.Event(), threading.Event()
    callbacks = Queue()
    threads = []
    peripheral = PeripheralBattery("Fixture keyboard", "Keyboard", 25, False, "")

    def read():
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return [peripheral]

    api.peripheral_batteries.side_effect = read
    applet = ui.BatteryApplet(48, Config(), platform_services=make_applet_platform())
    api.peripheral_batteries.assert_not_called()
    applet._worker = BackgroundWorker(
        idle_add=lambda callback, *args: callbacks.put((callback, args))
    )
    try:
        applet._refresh_peripherals()
        assert entered.wait(5)
        applet._refresh_peripherals()
        assert len(threads) == 1 and threads[0] != threading.get_ident()
        if lifecycle == "stopped":
            applet.stop()
        elif lifecycle == "replaced":
            applet._backend, _api = _backend()
        release.set()
        callback, args = callbacks.get(timeout=5)
        assert applet._peripherals == []
        callback(*args)
        assert applet._peripherals == ([peripheral] if lifecycle == "active" else [])
        assert not applet._worker._active_keys
    finally:
        release.set()
        applet.stop()


def test_peripheral_reply_from_a_previous_start_cannot_replace_current_readings(
    monkeypatch,
):
    from docking.applets.battery import applet as ui
    from docking.applets.battery.peripherals import PeripheralBattery
    from docking.core.config import Config

    value, _api = _backend()
    monkeypatch.setattr(
        TestAppletBackendFactories, "battery", staticmethod(lambda: value)
    )
    monkeypatch.setattr(ui.GLib, "timeout_add_seconds", lambda *_args: 77)
    monkeypatch.setattr(ui.GLib, "source_remove", lambda _source: None)
    applet = ui.BatteryApplet(48, Config(), platform_services=make_applet_platform())
    applet._worker = Mock()
    applet.start(lambda: None)
    previous = applet._worker.run_guarded.call_args.kwargs["on_result"]
    applet.stop()
    applet.start(lambda: None)
    current = applet._worker.run_guarded.call_args.kwargs["on_result"]
    old = PeripheralBattery("Previous device", "Keyboard", 10, False, "")
    new = PeripheralBattery("Current device", "Mouse", 50, False, "")
    current([new])
    previous([old])
    assert applet._peripherals == [new]
    applet.stop()


def test_sdk_structure_layout_and_signed_rate_match_both_windows_abis():
    assert ctypes.sizeof(windows.SystemPowerStatus) == 12
    assert windows.SystemPowerStatus.BatteryLifeTime.offset == 4
    assert ctypes.sizeof(windows.SystemBatteryState) == 32
    assert windows.SystemBatteryState.MaxCapacity.offset == 8
    assert windows.SystemBatteryState.Rate.offset == 16
    raw = bytearray(32)
    raw[16:20] = (-7600).to_bytes(4, "little", signed=True)
    assert windows.SystemBatteryState.from_buffer_copy(raw).Rate == -7600


def test_native_calls_use_output_structures_and_reject_failed_reads(monkeypatch):
    library = SimpleNamespace(
        GetSystemPowerStatus=Mock(), CallNtPowerInformation=Mock()
    )
    monkeypatch.setattr(
        windows.ctypes, "WinDLL", lambda *_a, **_kw: library, raising=False
    )
    monkeypatch.setattr(windows.ctypes, "get_last_error", lambda: 5, raising=False)

    def status(pointer):
        ctypes.cast(pointer, ctypes.POINTER(windows.SystemPowerStatus))[0] = _status()
        return 1

    def battery(level, input_buffer, input_size, output_buffer, output_size):
        assert (level, input_buffer, input_size, output_size) == (5, None, 0, 32)
        ctypes.cast(output_buffer, ctypes.POINTER(windows.SystemBatteryState))[0] = (
            windows.SystemBatteryState(BatteryPresent=1, Rate=-7600)
        )
        return 0

    library.GetSystemPowerStatus.side_effect = status
    library.CallNtPowerInformation.side_effect = battery
    api = windows.WindowsPowerAPI()
    assert api.power_status().BatteryLifePercent == 75
    assert api.battery_state().Rate == -7600
    library.GetSystemPowerStatus.side_effect = None
    library.GetSystemPowerStatus.return_value = 0
    library.CallNtPowerInformation.side_effect = None
    library.CallNtPowerInformation.return_value = -1073741790  # STATUS_ACCESS_DENIED
    assert api.power_status() is None and api.battery_state() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows power APIs")
def test_native_status_and_rate_are_readable_without_modifying_power_policy():
    api = windows.WindowsPowerAPI()
    status = api.power_status()
    battery = api.battery_state()
    assert status is not None and battery is not None
    assert status.ACLineStatus in {0, 1, 255}
    assert status.BatteryLifePercent in {*range(101), 255}
    assert battery.BatteryPresent in {0, 1}
    reading = windows.WindowsBatteryBackend(api=api).read_battery()
    if status.BatteryFlag != 255 and status.BatteryFlag & 128:
        assert reading is None
