"""Process evidence contract and OS selection."""

from abc import ABC, abstractmethod
from pathlib import Path

from docking.platform import environment


class ProcessBackend(ABC):
    @abstractmethod
    def executable(self, pid: int) -> Path | None: ...
    @abstractmethod
    def script(self, pid: int) -> str | None: ...
    @abstractmethod
    def sandbox_app_id(self, pid: int) -> str | None: ...
    @abstractmethod
    def steam_app_id(self, pid: int) -> str | None: ...


def create_backend(proc_root: Path = Path("/proc")) -> ProcessBackend:
    if environment.is_windows():
        from docking.platform.applications.windows.process import WindowsProcessBackend

        return WindowsProcessBackend()
    if environment.is_bsd() and proc_root == Path("/proc"):
        from docking.platform.applications.bsd import BsdProcessBackend

        return BsdProcessBackend()
    from docking.platform.applications.linux import LinuxProcessBackend

    return LinuxProcessBackend(proc_root)
