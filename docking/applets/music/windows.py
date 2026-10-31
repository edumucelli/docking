"""Native Windows implementation of the common Music backend."""

from __future__ import annotations

import threading
from dataclasses import replace

from docking.log import get_logger

from .backend import MusicBackend
from .state import MusicState, unavailable_state
from .windows_audio import WindowsPlayerAudio
from .windows_media import WindowsMediaAPI

log = get_logger("music.windows")


class WindowsMusicBackend(MusicBackend):
    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._api: WindowsMediaAPI | None = None
        self._audio: WindowsPlayerAudio | None = None

    def poll(self) -> MusicState:
        if self._cancelled.is_set():
            return unavailable_state()
        if self._api is None:
            self._api = WindowsMediaAPI(self._cancelled)
        state = self._api.poll()
        if not state.available:
            return state
        try:
            if self._audio is None:
                self._audio = WindowsPlayerAudio()
            level = self._audio.read(state.player_application_id)
        except OSError as exc:
            log.debug("Player volume unavailable: %s", exc)
            level = None
        return (
            replace(
                state,
                can_set_volume=True,
                volume_percent=level.percent,
                volume_id=level.identifier,
            )
            if level is not None
            else state
        )

    def _control(self, state: MusicState, action: str) -> bool:
        if self._cancelled.is_set() or self._api is None or not state.available:
            return False
        return self._api.control(state, action)

    def play_pause(self, state: MusicState) -> bool:
        return self._control(state, "play_pause")

    def next_track(self, state: MusicState) -> bool:
        return self._control(state, "next")

    def previous_track(self, state: MusicState) -> bool:
        return self._control(state, "previous")

    def set_volume(self, state: MusicState, volume_percent: int) -> bool:
        if (
            self._cancelled.is_set()
            or self._api is None
            or self._audio is None
            or not state.can_set_volume
        ):
            return False
        if not self._api.contains(state):
            return False
        return self._audio.set_volume(
            state.player_application_id, state.volume_id, volume_percent
        )

    def stop(self) -> None:
        self._cancelled.set()

    def close(self) -> None:
        if self._api is not None:
            self._api.close()
            self._api = None
