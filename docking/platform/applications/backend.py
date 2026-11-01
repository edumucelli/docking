"""Shared application platform contracts and their result records.

Composition supplies each capability independently. Native implementations own
platform I/O; importing these declarations must not initialize GUI services.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .associations import AssociationKey, AssociationRead
from .types import (
    ApplicationDiagnostic,
    ApplicationInfo,
    DiscoveryDecision,
    DiscoveryDirectoryDiagnostic,
    TransientApplicationInfo,
)

if TYPE_CHECKING:
    from .registry import ApplicationRegistry
    from .steam import SteamDiscoveryResult, SteamGame


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    applications: tuple[ApplicationInfo, ...]
    handles: dict[str, object]
    transient: tuple[TransientApplicationInfo, ...]
    transient_handles: dict[str, object]
    presentation_order: tuple[str, ...]
    directories: tuple[Path, ...] = ()
    diagnostics: tuple[ApplicationDiagnostic, ...] = ()
    decisions: tuple[DiscoveryDecision, ...] = ()
    directory_statuses: tuple[DiscoveryDirectoryDiagnostic, ...] = ()
    initial_discovery_pending: bool = False


class CatalogMonitor(ABC):
    @abstractmethod
    def start(self) -> None: ...
    @abstractmethod
    def sync(self, directories: tuple[Path, ...]) -> None: ...
    @abstractmethod
    def stop(self) -> None: ...


class ApplicationCatalogBackend(ABC):
    @property
    @abstractmethod
    def initial_discovery_pending(self) -> bool: ...
    @abstractmethod
    def invalidate(self) -> None: ...
    @abstractmethod
    def discover(self, *, handle_epoch: int) -> DiscoveryResult: ...
    @abstractmethod
    def steam(self) -> SteamDiscoveryResult: ...
    @abstractmethod
    def create_monitor(self, changed: Callable[[], None]) -> CatalogMonitor: ...
    @abstractmethod
    def game_application(self, game: SteamGame, /) -> ApplicationInfo: ...
    @abstractmethod
    def hidden_steam_id(self, path: Path) -> str | None: ...


class ApplicationAssociationBackend(ABC):
    @property
    @abstractmethod
    def revision(self) -> int: ...
    @abstractmethod
    def reads(
        self, keys: tuple[AssociationKey, ...]
    ) -> Iterable[tuple[AssociationKey, AssociationRead]]: ...
    @abstractmethod
    def association_only(self, application: ApplicationInfo) -> bool: ...
    def read(self, key: AssociationKey) -> AssociationRead:
        return next(iter(self.reads((key,))))[1]

    @abstractmethod
    def visible(self, handle: object, /) -> bool: ...
    @abstractmethod
    def registered(
        self, registry: ApplicationRegistry, handle: object, /
    ) -> ApplicationInfo | None: ...
    @abstractmethod
    def listing(
        self, registry: ApplicationRegistry, handle: object | None, /
    ) -> ApplicationInfo | TransientApplicationInfo | None: ...
    @abstractmethod
    def transient(
        self, app_info: object, listing_key: str
    ) -> TransientApplicationInfo: ...


class LaunchedProcessHandle(Protocol):
    """Opaque external process handle; both Popen and Win32 handles satisfy it."""

    @property
    def pid(self) -> int: ...
    def poll(self) -> int | None: ...


@dataclass(frozen=True, slots=True)
class LaunchedProcess:
    process: LaunchedProcessHandle
    executable_path: Path | None


@dataclass(frozen=True, slots=True)
class LaunchResult:
    accepted: bool
    launched: LaunchedProcess | None = None


ApplicationHandleSource = Callable[[], object | None]


class ApplicationLaunchBackend(ABC):
    @abstractmethod
    def launch(
        self,
        application: ApplicationInfo,
        handle: ApplicationHandleSource | None = None,
    ) -> LaunchResult: ...
    @abstractmethod
    def launch_action(
        self,
        application: ApplicationInfo,
        action_id: str,
        handle: ApplicationHandleSource,
    ) -> LaunchResult: ...
    @abstractmethod
    def launch_app_uris(
        self,
        application: ApplicationInfo,
        uris: Iterable[str],
        handle: ApplicationHandleSource,
    ) -> LaunchResult: ...
    @abstractmethod
    def launch_listing(
        self, listing_key: str, handle: object | None
    ) -> LaunchResult: ...


@dataclass(frozen=True, slots=True)
class GeneratedDesktopEntry:
    """A user-local launcher generated for a dropped executable."""

    desktop_id: str
    path: Path
    name: str
    icon_name: str


class ExecutableRegistrationBackend(ABC):
    @abstractmethod
    def clear_cache(self) -> None: ...
    @abstractmethod
    def generated_id(self, path: Path) -> str: ...
    @abstractmethod
    def register(
        self, target: str | Path, *, startup_wm_class: str = ""
    ) -> GeneratedDesktopEntry | None: ...
    @abstractmethod
    def desktop_id(self, target: str) -> str | None: ...
    @abstractmethod
    def appimage_needing_permission(self, target: str | Path) -> Path | None: ...
    @abstractmethod
    def make_executable(self, path: Path) -> bool: ...
    @abstractmethod
    def match_aliases(
        self, desktop_id: str, wm_class: str, exec_line: str
    ) -> list[str]: ...
    @abstractmethod
    def is_native_executable(self, path: Path) -> bool: ...


class ProcessBackend(ABC):
    @abstractmethod
    def executable(self, pid: int) -> Path | None: ...
    @abstractmethod
    def script(self, pid: int) -> str | None: ...
    @abstractmethod
    def sandbox_app_id(self, pid: int) -> str | None: ...
    @abstractmethod
    def steam_app_id(self, pid: int) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ApplicationSources:
    """Fresh capabilities whose catalog/association lifetimes are composed together.

    Consumers still receive each narrow backend independently. Native discovery
    and association acquisition may share one metadata worker owned by the
    catalog; this composition value never enters persisted state.
    """

    catalog: ApplicationCatalogBackend
    associations: ApplicationAssociationBackend
    registrations: ExecutableRegistrationBackend
