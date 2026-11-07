"""FreeBSD native audio controls through mixer."""

from docking.applets.volume.backend import VolumeControls

from .mixer import controls as mixer_controls


def controls() -> VolumeControls | None:
    return mixer_controls(
        tool="mixer",
        flag="-o",
        volume_key="vol.volume",
        mute_key="vol.mute",
        mute_toggle="toggle",
    )
