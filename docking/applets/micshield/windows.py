"""Windows microphone privacy state and aggregate capture-endpoint mute."""

from __future__ import annotations

import os
import threading
from typing import Protocol

from docking.applets.micshield.backend import MicShieldBackend
from docking.applets.micshield.state import MicShieldState
from docking.applets.micshield.windows_audio import CaptureEndpoint, WindowsCaptureAPI
from docking.log import get_logger

log = get_logger("micshield.windows")


class CaptureAPI(Protocol):
    def snapshot(self) -> tuple[CaptureEndpoint, ...]: ...
    def set_muted(self, muted: bool) -> bool: ...


class WindowsMicShieldBackend(MicShieldBackend):
    def __init__(self, *, api: CaptureAPI | None = None) -> None:
        self._api = api if api is not None else WindowsCaptureAPI()
        self._lock = threading.RLock()
        self._stopped = threading.Event()

    def stop(self) -> None:
        self._stopped.set()

    def get_state(self) -> MicShieldState:
        with self._lock:
            if self._stopped.is_set():
                return MicShieldState(False, False, False)
            try:
                endpoints = self._api.snapshot()
                if self._stopped.is_set() or not endpoints:
                    return MicShieldState(False, False, False)
                streams = tuple(stream for e in endpoints for stream in e.streams)
                muted = (
                    False
                    if any(e.muted is False for e in endpoints)
                    else None
                    if any(e.muted is None for e in endpoints)
                    else True
                )
                active = (
                    True
                    if streams
                    else False
                    if all(e.activity_known for e in endpoints)
                    else None
                )
                return MicShieldState(True, muted, active, streams)
            except OSError as exc:
                log.debug("Microphone state unavailable: %s", exc)
                return MicShieldState(False, None, None, error=True)

    def set_muted(self, muted: bool) -> bool:
        with self._lock:
            if self._stopped.is_set() or type(muted) is not bool:
                return False
            try:
                accepted = self._api.set_muted(muted)
                state = self.get_state()
                return (
                    not self._stopped.is_set()
                    and accepted
                    and state.available
                    and state.muted is muted
                )
            except OSError as exc:
                log.debug("Microphone mute failed: %s", exc)
                return False

    def toggle_muted(self) -> bool:
        with self._lock:
            state = self.get_state()
            # An unknown state chooses mute, never an unverified unmute.
            return self.set_muted(not state.muted) if state.available else False

    def settings_available(self) -> bool:
        return not self._stopped.is_set()

    def open_settings(self) -> bool:
        if self._stopped.is_set():
            return False
        try:
            os.startfile("ms-settings:privacy-microphone")
            return True
        except OSError as exc:
            log.debug("Microphone Settings unavailable: %s", exc)
            return False
