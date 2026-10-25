"""Volume backend contract and environment-based selection."""

from collections.abc import Callable
from typing import NamedTuple

from docking.applets.volume.state import VolumeState
from docking.platform import environment


class VolumeBackend(NamedTuple):
    """Audio backend with commands for reading/setting volume."""

    command: str
    get_state: Callable[[], VolumeState | None]
    set_volume: Callable[[int], None]
    toggle_mute: Callable[[], None]


def create_backend() -> VolumeBackend | None:
    if environment.is_windows():
        from docking.applets.volume.windows import create_backend
    elif environment.is_bsd():
        from docking.applets.volume.bsd import create_backend
    else:
        from docking.applets.volume.linux import create_backend
    return create_backend()
