"""Microphone operations contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .state import MicShieldState, MicStream


class MicshieldOperations(ABC):
    @abstractmethod
    def probe_mic_state(self) -> MicShieldState: ...

    @abstractmethod
    def toggle_mic_mute(self) -> bool: ...

    @abstractmethod
    def set_mic_muted(self, *, muted: bool) -> bool: ...

    @abstractmethod
    def input_source_names(self) -> tuple[str, ...]: ...

    @abstractmethod
    def active_source_outputs(self) -> tuple[MicStream, ...]: ...
