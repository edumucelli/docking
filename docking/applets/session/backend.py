"""Session action contract and OS selection."""

from abc import ABC, abstractmethod

from docking.platform import environment


class SessionBackend(ABC):
    @abstractmethod
    def available_actions(self) -> frozenset[str]: ...

    @abstractmethod
    def perform(self, action: str) -> bool:
        """Request an action; success means accepted, not a completed transition."""

    def stop(self) -> None:
        """Release applet-owned resources without undoing an accepted OS action."""
        return


def create_backend() -> SessionBackend:
    if environment.is_windows():
        from docking.applets.session.windows import WindowsSessionBackend

        return WindowsSessionBackend()
    if environment.is_bsd():
        from docking.applets.session.bsd import BsdSessionBackend

        return BsdSessionBackend()
    from docking.applets.session.linux import LinuxSessionBackend

    return LinuxSessionBackend()
