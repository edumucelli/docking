"""Volume backend contract and environment-based selection."""

from collections.abc import Callable
from typing import NamedTuple

from docking.applets.volume.state import VolumeState


class VolumeControls(NamedTuple):
    """Optional audio controls; settings remain available without a mixer."""

    command: str
    get_state: Callable[[], VolumeState | None]
    set_volume: Callable[[int], None]
    toggle_mute: Callable[[], None]


class VolumeSettings(NamedTuple):
    available: Callable[[], bool]
    open: Callable[[], bool]


class VolumeBackend(NamedTuple):
    """System integration with optional audio controls and settings actions."""

    controls: VolumeControls | None
    settings: VolumeSettings
