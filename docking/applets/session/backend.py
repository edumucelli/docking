"""Session action contract and OS selection."""

from abc import ABC, abstractmethod

from docking.platform import environment


class SessionBackend(ABC):
    @abstractmethod
    def command_for(self, action: str) -> list[str] | None: ...
    @abstractmethod
    def lock_commands(self) -> tuple[tuple[str, ...], ...]: ...


def create_backend() -> SessionBackend:
    if environment.is_bsd():
        from docking.applets.session.bsd import BsdSessionBackend

        return BsdSessionBackend()
    from docking.applets.session.linux import LinuxSessionBackend

    return LinuxSessionBackend()
