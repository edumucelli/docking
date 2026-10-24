"""Bluetooth backend contract and environment selection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from docking.platform import environment

if TYPE_CHECKING:
    from docking.applets.bluetooth.state import BluetoothState


@dataclass(frozen=True, slots=True)
class BluetoothCapabilities:
    trust: bool = True
    remove_pairing: bool = True


class BluetoothBackend(ABC):
    capabilities = BluetoothCapabilities()

    @abstractmethod
    def close(self) -> None:
        """Release work owned by this client when its applet stops."""

    @abstractmethod
    def get_state(self, active_adapter_path: str | None = None) -> BluetoothState: ...
    @abstractmethod
    def set_adapter_power(self, adapter_path: str, powered: bool) -> bool: ...
    @abstractmethod
    def start_discovery(self, adapter_path: str) -> bool: ...
    @abstractmethod
    def stop_discovery(self, adapter_path: str, quiet: bool = False) -> bool: ...
    @abstractmethod
    def connect_device(self, device_path: str) -> bool: ...
    @abstractmethod
    def disconnect_device(self, device_path: str) -> bool: ...
    @abstractmethod
    def pair_device(
        self, device_path: str, address: str = "", timeout_s: int = 20
    ) -> bool: ...
    @abstractmethod
    def remove_device(self, adapter_path: str, device_path: str) -> bool: ...
    @abstractmethod
    def set_trusted(self, device_path: str, trusted: bool) -> bool: ...


def create_backend() -> BluetoothBackend:
    if environment.is_bsd():
        from docking.applets.bluetooth.bsd import BsdBluetoothBackend

        return BsdBluetoothBackend()
    from docking.applets.bluetooth.linux import LinuxBluetoothBackend

    return LinuxBluetoothBackend()
