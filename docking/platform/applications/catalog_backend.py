"""Application catalog contract and platform selection."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from docking.platform import environment

from .associations import AssociationKey, AssociationRead
from .discovery import DiscoveryResult
from .metadata import (
    ApplicationChangeMonitor,
    ApplicationMetadataLookup,
    MetadataResolver,
)
from .registration import ExecutableRegistrationBackend
from .types import ApplicationInfo


@dataclass(frozen=True, slots=True)
class CatalogRequest:
    application_source: Callable[[], Iterable[object]]
    desktop_directories_source: Callable[[], Iterable[Path]]
    desktop_app_info_for_id: Callable[[str], Any]
    desktop_app_info_from_filename: Callable[[str], Any]
    handle_epoch: int
    catalog: tuple[ApplicationInfo, ...] | None
    source_discovery: DiscoveryResult | None = None


class ApplicationCatalogBackend(Protocol):
    def association_reads(
        self, keys: tuple[AssociationKey, ...], lookup: ApplicationMetadataLookup | None
    ) -> Iterable[tuple[AssociationKey, AssociationRead]]: ...

    def default_source(self) -> Iterable[object]: ...
    def discover(self, request: CatalogRequest) -> DiscoveryResult: ...
    def metadata_resolver_factory(
        self,
        registrations: ExecutableRegistrationBackend,
        /,
        *,
        read_default_source: bool,
    ) -> Callable[[], MetadataResolver] | None: ...
    def create_change_monitor(
        self,
        changed: Callable[[], None],
        /,
    ) -> ApplicationChangeMonitor | None: ...


def create_catalog_backend() -> ApplicationCatalogBackend:
    if environment.is_windows():
        from .windows.catalog import WindowsApplicationCatalogBackend

        return WindowsApplicationCatalogBackend()
    from .unix_catalog import UnixApplicationCatalogBackend

    return UnixApplicationCatalogBackend()
