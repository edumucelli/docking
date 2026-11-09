"""Select the native BSD audio controls, retaining PulseAudio preference."""

import shutil

from docking.applets.volume.backend import VolumeBackend, VolumeControls
from docking.applets.volume.implementations.unix.desktop_settings import SETTINGS
from docking.applets.volume.implementations.unix.pulseaudio import BACKEND as PULSEAUDIO
from docking.platform.environment import is_netbsd, is_openbsd


def create_backend() -> VolumeBackend:
    return VolumeBackend(_controls(), SETTINGS)


def _controls() -> VolumeControls | None:
    if shutil.which("pactl"):
        return PULSEAUDIO
    if is_netbsd():
        from .netbsd import controls
    elif is_openbsd():
        from .openbsd import controls
    else:
        from .freebsd import controls

    return controls()
