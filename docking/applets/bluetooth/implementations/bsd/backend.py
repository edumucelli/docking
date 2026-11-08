"""Select the native BSD Bluetooth backend."""

from docking.applets.bluetooth.backend import BluetoothBackend
from docking.platform.environment import is_netbsd, is_openbsd


def create_backend() -> BluetoothBackend:
    if is_netbsd():
        from .netbsd import create_backend as create_native
    elif is_openbsd():
        from .openbsd import create_backend as create_native
    else:
        from .freebsd import create_backend as create_native

    return create_native()
