"""OpenBSD native audio controls through sndioctl."""

from docking.applets.volume.backend import VolumeControls

from .mixer import controls as mixer_controls


def controls() -> VolumeControls | None:
    return mixer_controls(
        tool="sndioctl",
        flag="-n",
        volume_key="output.level",
        mute_key="output.mute",
        mute_toggle="!",
    )
