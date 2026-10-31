"""Volume backend contract and environment-based selection."""

from collections.abc import Callable
from typing import NamedTuple

from docking.applets.volume.state import VolumeState


class VolumeBackend(NamedTuple):
    """Audio backend with commands for reading/setting volume."""

    command: str
    get_state: Callable[[], VolumeState | None]
    set_volume: Callable[[int], None]
    toggle_mute: Callable[[], None]
