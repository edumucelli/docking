"""Camera state and explicit user actions shared by platform implementations."""

from abc import ABC, abstractmethod

from docking.applets.camshield.state import CamshieldState


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
