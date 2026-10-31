"""Microphone state and explicit mute controls shared by platform backends."""

from abc import ABC, abstractmethod

from docking.applets.micshield.state import MicShieldState
from docking.platform import environment


class MicShieldBackend(ABC):
    @abstractmethod
    def get_state(self) -> MicShieldState: ...

    @abstractmethod
    def set_muted(self, muted: bool) -> bool:
        """Change every current microphone input; success requires readback."""
        ...

    @abstractmethod
    def toggle_muted(self) -> bool: ...

    @abstractmethod
    def stop(self) -> None: ...

    def settings_available(self) -> bool:
        return False

    def open_settings(self) -> bool:
        return False


def create_backend() -> MicShieldBackend:
    if environment.is_windows():
        from docking.applets.micshield.windows import WindowsMicShieldBackend

        return WindowsMicShieldBackend()
    from docking.applets.micshield.linux import LinuxMicShieldBackend

    return LinuxMicShieldBackend()
