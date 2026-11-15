"""Composition-only platform provider; consumers receive narrow dependencies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.applets.aiusage.hooks import UsageHooks
    from docking.applets.platform import AppletBackendFactories
    from docking.core.config import Config
    from docking.platform.applications.backend import (
        ApplicationLaunchBackend,
        ApplicationSources,
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
    def usage_hooks(self) -> UsageHooks: ...
    @abstractmethod
    def applet_backends(self) -> AppletBackendFactories: ...
    @abstractmethod
    def application_sources(self) -> ApplicationSources: ...
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
    def session(
        self,
        *,
        config: Config,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
    ) -> SessionBackend:
        from docking.platform.backends.selection import create_session_backend

        return create_session_backend(
            config=config,
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
        )

    @abstractmethod
    def camera_helper(self, argv: list[str] | None = None) -> int: ...
