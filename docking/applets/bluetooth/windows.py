"""Windows implementation of the shared Bluetooth contract."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
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
    ClassicDevice,
    Radio,
    WindowsBluetoothAPI,
    address_text,
)
from docking.log import get_logger

log = get_logger("bluetooth.windows")


class RadioAPI(Protocol):
    def snapshot(self) -> tuple[Radio, ...]: ...
    def set_power(self, radio: Radio, powered: bool) -> bool: ...
    def remove_pairing(self, radio: Radio, device: ClassicDevice) -> bool: ...
    def start_discovery(self, radio: Radio) -> bool: ...
    def stop_discovery(self, identifier: str) -> bool: ...
    def close(self) -> None: ...


def _device_icon(device_class: int) -> str:
    return {
        1: "computer",
        2: "phone",
        4: "audio-headphones",
        5: "input-keyboard",
        6: "printer",
    }.get((device_class >> 8) & 0x1F, "bluetooth-active-symbolic")


def _file_sender() -> Path | None:
    executable = (
        Path(os.environ.get("SYSTEMROOT", "C:\\Windows")) / "System32/fsquirt.exe"
    )
    return executable if executable.is_file() else None


class WindowsBluetoothBackend(BluetoothBackend):
    # Windows owns trust, pairing UI and profile-specific connections.
    # Classic inquiries do not cover BLE.
    capabilities = BluetoothCapabilities(
        trust=False,
        remove_pairing=True,
        discovery=True,
        connect=False,
        disconnect=False,
        pair=False,
    )

    def __init__(self, *, api: RadioAPI | None = None) -> None:
        self._api = api if api is not None else WindowsBluetoothAPI()
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._api.close()

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
                    r.discovering,
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
        if self._closed.is_set():
            return ()
        actions = (BluetoothAction.DEVICES,)
        if _file_sender() is not None:
            actions = (BluetoothAction.SEND_FILES, *actions)
        return actions

    def open_desktop_action(self, action: BluetoothAction) -> bool:
        if self._closed.is_set() or action not in self.desktop_actions():
            return False
        try:
            if action == BluetoothAction.SEND_FILES:
                executable = _file_sender()
                if executable is None:
                    return False
                os.startfile(str(executable), arguments="-send")
            else:
                os.startfile("ms-settings:bluetooth")
            return True
        except OSError as exc:
            log.debug("Bluetooth desktop action %s unavailable: %s", action.value, exc)
            return False

    def start_discovery(self, adapter_path: str) -> bool:
        with self._lock:
            if self._closed.is_set():
                return False
            try:
                radio = next(
                    (r for r in self._api.snapshot() if r.identifier == adapter_path),
                    None,
                )
                if radio is None or radio.state != 1 or self._closed.is_set():
                    return False
                started = self._api.start_discovery(radio)
                return started and not self._closed.is_set()
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                log.debug("Bluetooth discovery failed: %s", exc)
                return False

    def stop_discovery(self, adapter_path: str, quiet: bool = False) -> bool:
        # Session cancellation only updates local API state. Do not wait for
        # the poll lock: GTK calls this during stop while an inquiry may run.
        try:
            return self._api.stop_discovery(adapter_path)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            if not quiet:
                log.debug("Bluetooth discovery cleanup failed: %s", exc)
            return False

    def connect_device(self, device_path: str) -> bool:
        return False

    def disconnect_device(self, device_path: str) -> bool:
        return False

    def pair_device(
        self, device_path: str, address: str = "", timeout_s: int = 20
    ) -> bool:
        return False

    def remove_device(self, adapter_path: str, device_path: str) -> bool:
        with self._lock:
            if self._closed.is_set():
                return False
            try:
                radio = next(
                    (r for r in self._api.snapshot() if r.identifier == adapter_path),
                    None,
                )
                if radio is None:
                    return False
                device = next(
                    (
                        d
                        for d in radio.devices
                        if f"{radio.identifier}/{d.address:012x}" == device_path
                        and d.paired
                    ),
                    None,
                )
                if device is None or self._closed.is_set():
                    return False
                if not self._api.remove_pairing(radio, device) or self._closed.is_set():
                    return False
                fresh = next(
                    (
                        r
                        for r in self._api.snapshot()
                        if r.identifier == radio.identifier
                        and r.address == radio.address
                    ),
                    None,
                )
                return (
                    not self._closed.is_set()
                    and fresh is not None
                    and not any(
                        d.address == device.address and d.paired for d in fresh.devices
                    )
                )
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                log.debug("Bluetooth pairing removal failed: %s", exc)
                return False

    def set_trusted(self, device_path: str, trusted: bool) -> bool:
        return False
