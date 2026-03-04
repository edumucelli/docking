"""Tests for Bluetooth applet and state helpers."""

from __future__ import annotations

from dataclasses import replace

import docking.applets.bluetooth.applet as bluetooth_applet_mod
import docking.applets.bluetooth.state as bluetooth_state_mod
from docking.applets.bluetooth import (
    BluetoothAdapterState,
    BluetoothApplet,
    BluetoothDeviceState,
    BluetoothState,
    BluezBackend,
    adapter_from_state,
    build_tooltip,
    connected_count,
    create_bluetooth_icon,
    device_menu_label,
    unavailable_state,
)


def _adapter(
    *,
    path: str = "/org/bluez/hci0",
    alias: str = "Adapter 0",
    powered: bool = True,
    discovering: bool = False,
) -> BluetoothAdapterState:
    return BluetoothAdapterState(
        path=path,
        name=alias,
        alias=alias,
        powered=powered,
        discovering=discovering,
        address="AA:BB:CC:DD:EE:FF",
    )


def _device(
    *,
    path: str,
    adapter_path: str = "/org/bluez/hci0",
    alias: str = "Device",
    paired: bool = True,
    trusted: bool = True,
    connected: bool = False,
    battery_percent: int | None = None,
) -> BluetoothDeviceState:
    return BluetoothDeviceState(
        path=path,
        adapter_path=adapter_path,
        name=alias,
        alias=alias,
        address="11:22:33:44:55:66",
        icon_name="audio-headphones",
        paired=paired,
        trusted=trusted,
        connected=connected,
        battery_percent=battery_percent,
        rssi=None,
    )


def _state(**overrides: object) -> BluetoothState:
    base = BluetoothState(
        available=True,
        adapters=(_adapter(),),
        devices=(),
        active_adapter_path="/org/bluez/hci0",
        error="",
    )
    values = {name: getattr(base, name) for name in BluetoothState.__dataclass_fields__}
    values.update(overrides)
    return BluetoothState(**values)


class TestBluetoothStateHelpers:
    def test_connected_count(self):
        state = _state(
            devices=(
                _device(path="/d/1", connected=True),
                _device(path="/d/2", connected=False),
            )
        )
        assert connected_count(state) == 1

    def test_adapter_from_state_prefers_selected(self):
        state = _state(
            adapters=(
                _adapter(path="/org/bluez/hci0", alias="A"),
                _adapter(path="/org/bluez/hci1", alias="B"),
            )
        )
        assert adapter_from_state(state, "/org/bluez/hci1") == "/org/bluez/hci1"

    def test_adapter_from_state_falls_back_to_powered(self):
        state = _state(
            adapters=(
                _adapter(path="/org/bluez/hci0", alias="A", powered=False),
                _adapter(path="/org/bluez/hci1", alias="B", powered=True),
            )
        )
        assert adapter_from_state(state, "/invalid") == "/org/bluez/hci1"

    def test_build_tooltip_includes_counts_and_discovery(self):
        state = _state(
            adapters=(_adapter(discovering=True),),
            devices=(
                _device(path="/d/1", paired=True, connected=True, battery_percent=80),
                _device(path="/d/2", paired=True, connected=False),
            ),
        )
        text = build_tooltip(state, "/org/bluez/hci0")
        assert "Bluetooth: On" in text
        assert "Connected: 1" in text
        assert "Paired: 2" in text
        assert "Discovering: Yes" in text
        assert "Battery:" in text

    def test_build_tooltip_unavailable(self):
        assert "No adapter" in build_tooltip(unavailable_state(), "")

    def test_device_menu_label(self):
        label = device_menu_label(
            _device(path="/d", connected=True, paired=True, battery_percent=77)
        )
        assert "Connected" in label
        assert "Paired" in label
        assert "77%" in label


class TestBluezBackend:
    def test_pair_device_uses_bluetoothctl_fallback_on_failure(self):
        backend = object.__new__(BluezBackend)
        backend._call_method = lambda **kwargs: False  # type: ignore[attr-defined]
        backend._pair_with_bluetoothctl = (  # type: ignore[attr-defined]
            lambda **kwargs: True
        )
        assert backend.pair_device("/org/bluez/hci0/dev_x", address="AA:BB") is True

    def test_set_adapter_power_off_stops_discovery_and_retries(self, monkeypatch):
        backend = object.__new__(BluezBackend)
        calls: list[str] = []
        attempts = [False, True]
        backend.stop_discovery = (  # type: ignore[attr-defined]
            lambda path, quiet=False: calls.append(f"stop:{path}:{quiet}") or True
        )
        backend._wait_for_discovery_state = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("wait") or True
        )
        backend._set_power_with_bluetoothctl = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("fallback") or False
        )
        backend._disconnect_connected_devices = (  # type: ignore[attr-defined]
            lambda adapter_path: calls.append(f"disconnect:{adapter_path}")
        )
        backend._get_adapter_props = lambda **kwargs: {  # type: ignore[attr-defined]
            "Discovering": False
        }
        backend._set_property = lambda **kwargs: calls.append("set") or attempts.pop(0)  # type: ignore[attr-defined]
        monkeypatch.setattr(bluetooth_state_mod.time, "sleep", lambda _s: None)

        assert backend.set_adapter_power("/org/bluez/hci0", False) is True
        assert calls[0] == "stop:/org/bluez/hci0:True"
        assert "wait" in calls
        assert calls.count("set") == 2
        assert "disconnect:/org/bluez/hci0" not in calls
        assert "fallback" not in calls

    def test_set_adapter_power_off_disconnects_on_repeated_failure(self, monkeypatch):
        backend = object.__new__(BluezBackend)
        calls: list[str] = []
        attempts = [False, False, False, False, False, False, False, False]
        backend.stop_discovery = (  # type: ignore[attr-defined]
            lambda path, quiet=False: calls.append(f"stop:{path}:{quiet}") or True
        )
        backend._wait_for_discovery_state = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("wait") or True
        )
        backend._set_power_with_bluetoothctl = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("fallback") or True
        )
        backend._disconnect_connected_devices = (  # type: ignore[attr-defined]
            lambda adapter_path: calls.append(f"disconnect:{adapter_path}")
        )
        backend._get_adapter_props = lambda **kwargs: {  # type: ignore[attr-defined]
            "Discovering": False
        }
        backend._set_property = lambda **kwargs: calls.append("set") or attempts.pop(0)  # type: ignore[attr-defined]
        monkeypatch.setattr(bluetooth_state_mod.time, "sleep", lambda _s: None)

        assert backend.set_adapter_power("/org/bluez/hci0", False) is True
        assert "disconnect:/org/bluez/hci0" in calls
        assert calls.count("set") == 8
        assert calls.count("wait") == 2
        assert "fallback" in calls

    def test_set_adapter_power_on_uses_fallback_when_needed(self):
        backend = object.__new__(BluezBackend)
        backend._set_property = lambda **kwargs: False  # type: ignore[attr-defined]
        backend._set_power_with_bluetoothctl = lambda **kwargs: True  # type: ignore[attr-defined]
        assert backend.set_adapter_power("/org/bluez/hci0", True) is True

    def test_set_adapter_power_off_fails_fast_when_discovery_is_external(
        self, monkeypatch
    ):
        backend = object.__new__(BluezBackend)
        calls: list[str] = []
        backend.stop_discovery = (  # type: ignore[attr-defined]
            lambda path, quiet=False: calls.append(f"stop:{path}:{quiet}") or True
        )
        backend._wait_for_discovery_state = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("wait") or False
        )
        backend._get_adapter_props = lambda **kwargs: {  # type: ignore[attr-defined]
            "Discovering": True
        }
        backend._set_property = lambda **kwargs: calls.append("set") or False  # type: ignore[attr-defined]
        backend._set_power_with_bluetoothctl = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("fallback") or False
        )
        backend._disconnect_connected_devices = lambda **kwargs: calls.append(
            "disconnect"
        )  # type: ignore[attr-defined]
        monkeypatch.setattr(bluetooth_state_mod.time, "sleep", lambda _s: None)

        assert backend.set_adapter_power("/org/bluez/hci0", False) is False
        assert calls.count("set") == 1
        assert "disconnect" not in calls
        assert "fallback" in calls


class _StubBackend:
    def __init__(self, initial_state: BluetoothState) -> None:
        self.state = initial_state
        self.power_calls: list[tuple[str, bool]] = []
        self.discovery_calls: list[str] = []

    def get_state(self, active_adapter_path: str | None = None) -> BluetoothState:
        if active_adapter_path:
            self.state = replace(self.state, active_adapter_path=active_adapter_path)
        return self.state

    def set_adapter_power(self, adapter_path: str, powered: bool) -> bool:
        self.power_calls.append((adapter_path, powered))
        adapters = tuple(
            replace(adapter, powered=powered)
            if adapter.path == adapter_path
            else adapter
            for adapter in self.state.adapters
        )
        self.state = replace(self.state, adapters=adapters)
        return True

    def start_discovery(self, adapter_path: str) -> bool:
        self.discovery_calls.append(adapter_path)
        return True

    def stop_discovery(self, adapter_path: str, quiet: bool = False) -> bool:
        _ = quiet
        return True

    def connect_device(self, device_path: str) -> bool:
        _ = device_path
        return True

    def disconnect_device(self, device_path: str) -> bool:
        _ = device_path
        return True

    def pair_device(
        self, device_path: str, address: str = "", timeout_s: int = 20
    ) -> bool:
        _ = (device_path, address, timeout_s)
        return True

    def remove_device(self, adapter_path: str, device_path: str) -> bool:
        _ = (adapter_path, device_path)
        return True

    def set_trusted(self, device_path: str, trusted: bool) -> bool:
        _ = (device_path, trusted)
        return True


def _make_applet(
    monkeypatch,
    state: BluetoothState,
) -> tuple[BluetoothApplet, _StubBackend]:
    backend = _StubBackend(initial_state=state)
    monkeypatch.setattr(bluetooth_applet_mod, "BluezBackend", lambda: backend)
    applet = BluetoothApplet(48)
    return applet, backend


class TestBluetoothApplet:
    def test_creates_with_icon(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        assert applet.item.icon is not None

    def test_click_toggles_adapter_power(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state())
        applet._set_power_async = lambda *, target: backend.set_adapter_power(  # type: ignore[assignment]
            "/org/bluez/hci0",
            target,
        )
        applet.on_clicked()
        assert backend.power_calls
        assert backend.power_calls[0] == ("/org/bluez/hci0", False)

    def test_power_off_temporarily_suppresses_discovery(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._on_power_result = lambda *args: False  # type: ignore[assignment]
        monkeypatch.setattr(bluetooth_applet_mod.time, "monotonic", lambda: 100.0)
        applet.on_clicked()
        assert applet._suppress_discovery_until > 100.0

    def test_power_result_sets_busy_error_message(self, monkeypatch):
        applet, _backend = _make_applet(
            monkeypatch,
            _state(adapters=(_adapter(discovering=True),)),
        )
        applet._power_transition_in_progress = True
        applet.refresh_presentation = lambda: None  # type: ignore[assignment]

        applet._on_power_result(
            False, False, _state(adapters=(_adapter(discovering=True),))
        )

        assert "blocked" in applet._action_error.lower()
        assert applet._power_transition_in_progress is False

    def test_menu_contains_expected_sections(self, monkeypatch):
        state = _state(
            adapters=(
                _adapter(path="/org/bluez/hci0", alias="A0"),
                _adapter(path="/org/bluez/hci1", alias="A1"),
            ),
            devices=(
                _device(path="/d/1", connected=True, paired=True),
                _device(path="/d/2", connected=False, paired=True),
                _device(path="/d/3", connected=False, paired=False),
            ),
        )
        applet, _backend = _make_applet(monkeypatch, state)
        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]
        assert "General" in labels
        assert "Bluetooth On" in labels
        assert "Continuous Discovery" in labels
        assert "Adapter" in labels
        assert "Connected Devices" in labels
        assert "Paired Devices" in labels
        assert "Discovered Devices" in labels
        assert "Refresh Now" in labels

        assert labels.index("Connected Devices") < labels.index("Paired Devices")
        assert labels.index("Paired Devices") < labels.index("Discovered Devices")

    def test_empty_groups_do_not_add_none_entries(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(devices=()))
        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]
        assert "None" not in labels

    def test_scroll_is_noop(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet.on_scroll(direction_up=True)


class TestBluetoothRender:
    def test_icon_renders(self):
        for size in (32, 48, 64):
            pixbuf = create_bluetooth_icon(
                size=size,
                available=True,
                powered=True,
                discovering=True,
                connected_devices=2,
            )
            assert pixbuf is not None
            assert pixbuf.get_width() == size
            assert pixbuf.get_height() == size
