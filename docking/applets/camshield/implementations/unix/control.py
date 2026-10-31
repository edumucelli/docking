"""Camera inspection and privileged action contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

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
