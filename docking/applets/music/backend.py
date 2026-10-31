"""Common media-player discovery, metadata and targeted controls."""

from abc import ABC, abstractmethod

from docking.platform import environment

from .state import MusicState


class MusicBackend(ABC):
    """All IO runs on a worker; only value objects cross into GTK."""

    @abstractmethod
    def poll(self) -> MusicState: ...

    @abstractmethod
    def play_pause(self, state: MusicState) -> bool: ...

    @abstractmethod
    def next_track(self, state: MusicState) -> bool: ...

    @abstractmethod
    def previous_track(self, state: MusicState) -> bool: ...

    @abstractmethod
    def set_volume(self, state: MusicState, volume_percent: int) -> bool: ...

    def application_ids(self, state: MusicState) -> tuple[str, ...]:
        """Pure registry candidates for resolving the player's icon on GTK."""
        return (state.player_application_id,) if state.player_application_id else ()

    def close(self) -> None:
        """Release native resources on the IO thread after its last request."""
        return

    @abstractmethod
    def stop(self) -> None: ...


def create_backend() -> MusicBackend:
    if environment.is_windows():
        from .windows import WindowsMusicBackend

        return WindowsMusicBackend()
    from .linux import HybridBackend

    return HybridBackend()
