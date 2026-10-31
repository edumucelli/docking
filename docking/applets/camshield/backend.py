"""Camera state and explicit user actions shared by platform implementations."""

from abc import ABC, abstractmethod

from docking.applets.camshield.state import CamshieldState
from docking.platform import environment


class CamshieldBackend(ABC):
    @abstractmethod
    def get_state(self) -> CamshieldState: ...

    @abstractmethod
    def controls_available(self) -> bool: ...

    def lock_available(self) -> bool:
        return self.controls_available()

    def unlock_available(self) -> bool:
        return self.controls_available()

    @abstractmethod
    def lock(self) -> bool: ...

    @abstractmethod
    def unlock(self) -> bool: ...

    @abstractmethod
    def stop(self) -> None: ...

    def settings_available(self) -> bool:
        return False

    def open_settings(self) -> bool:
        return False


def create_backend() -> CamshieldBackend:
    if environment.is_windows():
        from docking.applets.camshield.windows import WindowsCamshieldBackend

        return WindowsCamshieldBackend()
    if environment.is_bsd():
        from docking.applets.camshield.bsd import BsdCamshieldBackend

        return BsdCamshieldBackend()
    from docking.applets.camshield.linux import LinuxCamshieldBackend

    return LinuxCamshieldBackend()
