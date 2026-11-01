"""Bluetooth backend contract and environment selection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.applets.bluetooth.state import BluetoothState


@dataclass(frozen=True, slots=True)
class BluetoothCapabilities:
    trust: bool = True
    remove_pairing: bool = True
    power: bool = True
    discovery: bool = True
    connect: bool = True
    disconnect: bool = True
    pair: bool = True
    connect_label: str | None = None
    disconnect_label: str | None = None


class BluetoothAction(Enum):
    SEND_FILES = "send_files"
    DEVICES = "devices"
    ADAPTERS = "adapters"
    LOCAL_SERVICES = "local_services"


class BluetoothBackend(ABC):
    capabilities = BluetoothCapabilities()

    def device_capabilities(self, device_path: str) -> BluetoothCapabilities:
        """Read controls from published state, without device IO on GTK."""
        return self.capabilities

    def desktop_actions(self) -> tuple[BluetoothAction, ...]:
        return ()

    def open_desktop_action(self, action: BluetoothAction) -> bool:
        return False

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
