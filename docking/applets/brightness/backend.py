"""Brightness controls shared by the platform implementations."""

from abc import ABC, abstractmethod

from docking.applets.brightness.state import BrightnessState
from docking.platform import environment


class BrightnessBackend(ABC):
    @abstractmethod
    def get_state(self) -> BrightnessState: ...

    @abstractmethod
    def set_brightness(self, *, value: float, target: str) -> BrightnessState:
        """Apply to the named display and return the actual current state."""
        ...

    def stop(self) -> None:
        """Prevent new requests after the applet has been removed."""
        return


def create_backend() -> BrightnessBackend:
    if environment.is_windows():
        from docking.applets.brightness.windows import WindowsBrightnessBackend

        return WindowsBrightnessBackend()
    from docking.applets.brightness.linux import LinuxBrightnessBackend

    return LinuxBrightnessBackend()
