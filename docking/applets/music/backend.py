"""Media controller contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .state import MusicState


class MusicBackend(ABC):
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
