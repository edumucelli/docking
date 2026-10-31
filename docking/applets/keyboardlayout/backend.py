"""Input-source contract shared by the platform implementations."""

from abc import ABC, abstractmethod

from docking.applets.keyboardlayout.state import LayoutState
from docking.platform import environment


class LayoutBackend(ABC):
    name: str

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def query(self) -> LayoutState: ...

    @abstractmethod
    def switch(self, layout_code: str, *, context: str = "") -> None:
        """Request a source for the input context that produced the snapshot."""
        ...

    def prepare_interaction(self, state: LayoutState) -> LayoutState:
        """Refresh the input context before a menu can temporarily take focus."""
        return state

    def settings_available(self) -> bool:
        return False

    def viewer_available(self, layout_code: str) -> bool:
        return False

    def open_settings(self) -> bool:
        return False

    def show_layout(self, layout_code: str) -> bool:
        return False

    def stop(self) -> None:
        return


def create_backend() -> LayoutBackend:
    if environment.is_windows():
        from docking.applets.keyboardlayout.windows import WindowsKeyboardBackend

        return WindowsKeyboardBackend()
    from docking.applets.keyboardlayout.linux import LinuxKeyboardBackend

    return LinuxKeyboardBackend()
