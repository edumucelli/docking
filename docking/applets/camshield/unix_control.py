"""Unix camera device inspection and privileged helper operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from docking.platform import environment

if TYPE_CHECKING:
    from docking.applets.camshield.state import CamshieldState


class UnixCameraControl(ABC):
    @abstractmethod
    def requires_acl_backup(self, device: Path) -> bool: ...

    @abstractmethod
    def probe(self, *, dev_root: Path, proc_root: Path) -> CamshieldState: ...
    @abstractmethod
    def lock(self) -> int: ...
    @abstractmethod
    def unlock(self) -> int: ...
    @abstractmethod
    def status(self) -> int: ...
    @abstractmethod
    def acl_commands(
        self, device: Path, acl_file: Path
    ) -> tuple[list[str], list[list[str]]]: ...


def create_control() -> UnixCameraControl:
    if environment.is_windows():
        raise OSError("Unix camera device helper is unavailable on Windows")
    if environment.is_bsd():
        from docking.applets.camshield.bsd import BsdCameraControl

        return BsdCameraControl()
    from docking.applets.camshield.linux import LinuxCameraControl

    return LinuxCameraControl()
