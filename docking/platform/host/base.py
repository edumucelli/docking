"""Headless host contract. Importing it must not initialize the GUI."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from enum import Enum
from pathlib import Path


class HostFamily(Enum):
    LINUX = "linux"
    BSD = "bsd"


class HostServices(ABC):
    @property
    @abstractmethod
    def platform_name(self) -> str: ...
    @property
    @abstractmethod
    def family(self) -> HostFamily: ...

    @abstractmethod
    def mount_points(self) -> tuple[Path, ...]: ...
    @abstractmethod
    def prepare_process(self) -> None: ...
    @abstractmethod
    def local_path(self, target: str) -> Path | None: ...
    @abstractmethod
    def is_explicit_path(self, target: str) -> bool: ...
    @abstractmethod
    def config_home(self) -> Path: ...
    @abstractmethod
    def cache_home(self) -> Path: ...
    @abstractmethod
    def data_home(self) -> Path: ...
    @abstractmethod
    def state_home(self) -> Path: ...
    @abstractmethod
    def flush_directory(self, path: Path) -> None: ...
    @abstractmethod
    def writer_lock(self, fd: int, *, path: Path) -> AbstractContextManager[None]: ...
    @abstractmethod
    def hook_argv(self, root: Path, executable: str, provider: str) -> list[str]: ...
    @abstractmethod
    def hook_command(self, root: Path, executable: str) -> str: ...
