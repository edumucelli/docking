"""Select the native BSD camera permission control."""

from docking.applets.camshield.implementations.unix.control import UnixCameraControl
from docking.platform.environment import is_netbsd, is_openbsd


def create_backend() -> UnixCameraControl:
    if is_netbsd():
        from .netbsd import NetBsdCameraControl as NativeControl
    elif is_openbsd():
        from .openbsd import OpenBsdCameraControl as NativeControl
    else:
        from .freebsd import FreeBsdCameraControl as NativeControl

    return NativeControl()
