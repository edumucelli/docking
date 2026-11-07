"""Bluetooth behavior for BSD hosts without a supported integration."""

from docking.applets.bluetooth.backend import BluetoothBackend, BluetoothCapabilities
from docking.applets.bluetooth.state import BluetoothState, unavailable_state


class UnavailableBluetoothBackend(BluetoothBackend):
    capabilities = BluetoothCapabilities(
        trust=False,
        remove_pairing=False,
        power=False,
        discovery=False,
        connect=False,
        disconnect=False,
        pair=False,
    )

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def get_state(self, active_adapter_path: str | None = None) -> BluetoothState:
        return unavailable_state(self._reason)

    def close(self) -> None:
        pass

    def set_adapter_power(self, adapter_path: str, powered: bool) -> bool:
        return False

    def start_discovery(self, adapter_path: str) -> bool:
        return False

    def stop_discovery(self, adapter_path: str, quiet: bool = False) -> bool:
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
        return False

    def set_trusted(self, device_path: str, trusted: bool) -> bool:
        return False
