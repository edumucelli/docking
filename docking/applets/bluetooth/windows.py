"""Windows implementation of the shared Bluetooth contract."""

from __future__ import annotations

import os
import subprocess
import threading
from typing import Protocol

from docking.applets.bluetooth.backend import (
    BluetoothAction,
    BluetoothBackend,
    BluetoothCapabilities,
)
from docking.applets.bluetooth.state import (
    BluetoothAdapterState,
    BluetoothDeviceState,
    BluetoothState,
    _device_sort_key,
    adapter_from_state,
    unavailable_state,
)
from docking.applets.bluetooth.windows_radio import (
    Radio,
    WindowsBluetoothAPI,
    address_text,
)
from docking.log import get_logger

log = get_logger("bluetooth.windows")


class RadioAPI(Protocol):
    def snapshot(self) -> tuple[Radio, ...]: ...
    def set_power(self, radio: Radio, powered: bool) -> bool: ...


def _device_icon(device_class: int) -> str:
    return {
        1: "computer",
        2: "phone",
        4: "audio-headphones",
        5: "input-keyboard",
        6: "printer",
    }.get((device_class >> 8) & 0x1F, "bluetooth-active-symbolic")


class WindowsBluetoothBackend(BluetoothBackend):
    # Windows owns trust, pairing and profile-specific connection management.
    # Classic cached enumeration is not discovery and does not cover BLE.
    capabilities = BluetoothCapabilities(
        trust=False,
        remove_pairing=False,
        discovery=False,
        connect=False,
        disconnect=False,
        pair=False,
    )

    def __init__(self, *, api: RadioAPI | None = None) -> None:
        self._api = api if api is not None else WindowsBluetoothAPI()
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def close(self) -> None:
        self._closed.set()

    def get_state(self, active_adapter_path: str | None = None) -> BluetoothState:
        with self._lock:
            if self._closed.is_set():
                return unavailable_state()
            try:
                radios = self._api.snapshot()
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                log.debug("Bluetooth inventory unavailable: %s", exc)
                return unavailable_state("Windows Bluetooth query failed")
            if self._closed.is_set():
                return unavailable_state()
            adapters = tuple(
                BluetoothAdapterState(
                    r.identifier,
                    r.name,
                    r.name,
                    None if r.state == 0 else r.state == 1,
                    False,
                    address_text(r.address),
                    r.state in (1, 2),
                )
                for r in radios
            )
            devices = tuple(
                sorted(
                    (
                        BluetoothDeviceState(
                            f"{r.identifier}/{d.address:012x}",
                            r.identifier,
                            d.name,
                            d.name,
                            address_text(d.address),
                            _device_icon(d.device_class),
                            d.paired,
                            False,
                            d.connected,
                            None,
                            None,
                        )
                        for r in radios
                        for d in r.devices
                    ),
                    key=_device_sort_key,
                )
            )
            state = BluetoothState(bool(adapters), adapters, devices, "")
            selected = adapter_from_state(state, active_adapter_path) or ""
            return BluetoothState(state.available, adapters, devices, selected)

    def set_adapter_power(self, adapter_path: str, powered: bool) -> bool:
        with self._lock:
            if self._closed.is_set() or type(powered) is not bool:
                return False
            try:
                radio = next(
                    (r for r in self._api.snapshot() if r.identifier == adapter_path),
                    None,
                )
                if radio is None or radio.state not in (1, 2) or self._closed.is_set():
                    return False
                if (radio.state == 1) == powered:
                    return True
                if not self._api.set_power(radio, powered) or self._closed.is_set():
                    return False
                # An accepted operation is not proof of a completed change.
                return any(
                    r.identifier == radio.identifier
                    and r.address == radio.address
                    and (r.state == 1 if powered else r.state == 2)
                    for r in self._api.snapshot()
                )
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                log.debug("Bluetooth radio change failed: %s", exc)
                return False

    def desktop_actions(self) -> tuple[BluetoothAction, ...]:
        return () if self._closed.is_set() else (BluetoothAction.DEVICES,)

    def open_desktop_action(self, action: BluetoothAction) -> bool:
        if self._closed.is_set() or action not in self.desktop_actions():
            return False
        try:
            os.startfile("ms-settings:bluetooth")
            return True
        except OSError as exc:
            log.debug("Bluetooth Settings unavailable: %s", exc)
            return False

    def start_discovery(self, adapter_path: str) -> bool:
        return False

    def stop_discovery(self, adapter_path: str, quiet: bool = False) -> bool:
        return True  # This client never owns discovery.

    def connect_device(self, device_path: str) -> bool:
        return False

    def disconnect_device(self, device_path: str) -> bool:
        return False

    def pair_device(
        self, device_path: str, address: str = "", timeout_s: int = 20
    ) -> bool:
        return False

    def remove_device(self, adapter_path: str, device_path: str) -> bool:
        return False

    def set_trusted(self, device_path: str, trusted: bool) -> bool:
        return False
