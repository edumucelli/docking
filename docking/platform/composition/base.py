"""Composition-only platform provider; consumers receive narrow dependencies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.applets.platform import AppletBackendFactories
    from docking.core.config import Config
    from docking.platform.applications.backend import (
        ApplicationAssociationBackend,
        ApplicationCatalogBackend,
        ApplicationLaunchBackend,
        ExecutableRegistrationBackend,
        ProcessBackend,
    )
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.base import SessionBackend
    from docking.platform.desktop.base import DesktopServices
    from docking.platform.model import DockModel
    from docking.platform.tray.backend import TrayBackend
    from docking.search.services.platform import SearchIntegration


class PlatformProvider(ABC):
    @abstractmethod
    def applet_backends(self) -> AppletBackendFactories: ...
    @abstractmethod
    def catalog(self) -> ApplicationCatalogBackend: ...
    @abstractmethod
    def associations(self) -> ApplicationAssociationBackend: ...
    @abstractmethod
    def registrations(self) -> ExecutableRegistrationBackend: ...
    @abstractmethod
    def launches(self) -> ApplicationLaunchBackend: ...
    @abstractmethod
    def processes(self) -> ProcessBackend: ...
    @abstractmethod
    def desktop(self) -> DesktopServices: ...
    @abstractmethod
    def tray_backend(self) -> TrayBackend: ...
    @abstractmethod
    def search(self) -> SearchIntegration: ...
    @abstractmethod
    def session(
        self,
        *,
        config: Config,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
    ) -> SessionBackend: ...
    @abstractmethod
    def camera_helper(self, argv: list[str] | None = None) -> int: ...
