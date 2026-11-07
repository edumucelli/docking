"""OpenBSD Bluetooth availability."""

from docking.applets.bluetooth.backend import BluetoothBackend

from .unavailable import UnavailableBluetoothBackend


def create_backend() -> BluetoothBackend:
    return UnavailableBluetoothBackend("OpenBSD has no Bluetooth stack")
