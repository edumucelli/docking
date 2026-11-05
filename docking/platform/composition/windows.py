"""Windows construction root for host, application, session and applet capabilities."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from docking.applets.platform import AppletBackendFactories
from docking.platform.composition.base import PlatformProvider
from docking.platform.host.selection import host_services

if TYPE_CHECKING:
    from docking.applets.aiusage.hooks import UsageHooks
    from docking.applets.battery.backend import BatteryBackend
    from docking.applets.bluetooth.backend import BluetoothBackend
    from docking.applets.brightness.backend import BrightnessBackend
    from docking.applets.caffeine.inhibit import Inhibitor
    from docking.applets.camshield.backend import CamshieldBackend
    from docking.applets.capslock.backend import LockKeyBackend
    from docking.applets.keyboardlayout.backend import LayoutBackend
    from docking.applets.micshield.backend import MicShieldBackend
    from docking.applets.music.backend import MusicBackend
    from docking.applets.network.backend import NetworkBackend
    from docking.applets.notifications.backend import NotificationsBackend
    from docking.applets.popup import PopupAnchor
    from docking.applets.powerprofiles.backend import PowerProfilesControlBackend
    from docking.applets.screenshot.backend import ScreenshotBackend
    from docking.applets.session.backend import SessionBackend as SessionActionBackend
    from docking.applets.systemmonitor.backend import SystemMonitorBackend
    from docking.applets.systemmonitor.gpu import GpuSource
    from docking.applets.systemtray.presentation import TrayPresentation
    from docking.applets.thermals.backend import ThermalsBackend
    from docking.applets.trash.backend import TrashBackend
    from docking.applets.volume.backend import VolumeBackend
    from docking.applets.webapp.backend import BrowserFactory
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
    from docking.platform.tray.state import TrayState
    from docking.search.services.platform import SearchIntegration


class WindowsAppletBackendFactories(AppletBackendFactories):
    def battery(self) -> BatteryBackend:
        from docking.applets.battery.implementations.windows.backend import (
            WindowsBatteryBackend,
        )

        return WindowsBatteryBackend()

    def bluetooth(self) -> BluetoothBackend:
        from docking.applets.bluetooth.implementations.windows.backend import (
            WindowsBluetoothBackend,
        )

        return WindowsBluetoothBackend()

    def camshield(self) -> CamshieldBackend:
        from docking.applets.camshield.implementations.windows.backend import (
            WindowsCamshieldBackend,
        )

        return WindowsCamshieldBackend()

    def network(self, interface: str = "") -> NetworkBackend:
        from docking.applets.network.implementations.windows.backend import (
            WindowsNetworkBackend,
        )

        return WindowsNetworkBackend(interface)

    def volume(self) -> VolumeBackend | None:
        from docking.applets.volume.implementations.windows.backend import (
            create_backend,
        )

        return create_backend()

    def systemmonitor(self) -> SystemMonitorBackend:
        from docking.applets.systemmonitor.implementations.windows.backend import (
            WindowsSystemMonitorBackend,
        )

        return WindowsSystemMonitorBackend()

    def thermals(self) -> ThermalsBackend:
        from docking.applets.thermals.implementations.windows.backend import (
            WindowsThermalsBackend,
        )

        return WindowsThermalsBackend()

    def caffeine(self) -> Inhibitor:
        from docking.applets.caffeine.implementations.windows.backend import (
            WindowsInhibitor,
        )

        return WindowsInhibitor()

    def music(self) -> MusicBackend:
        from docking.applets.music.implementations.windows.audio import (
            WindowsPlayerAudio,
        )
        from docking.applets.music.implementations.windows.backend import (
            WindowsMusicBackend,
        )
        from docking.platform.backends.windows.windows import Win32WindowAPI

        return WindowsMusicBackend(
            audio_factory=lambda: WindowsPlayerAudio(
                window_source_factory=Win32WindowAPI
            )
        )

    def capslock(self) -> LockKeyBackend:
        from docking.applets.capslock.implementations.windows.backend import (
            WindowsLockKeyBackend,
        )

        return WindowsLockKeyBackend()

    def brightness(self) -> BrightnessBackend:
        from docking.applets.brightness.implementations.windows.backend import (
            WindowsBrightnessBackend,
        )

        return WindowsBrightnessBackend()

    def keyboardlayout(self) -> LayoutBackend:
        from docking.applets.keyboardlayout.implementations.windows.backend import (
            WindowsKeyboardBackend,
        )

        return WindowsKeyboardBackend()

    def notifications(self) -> NotificationsBackend:
        from docking.applets.notifications.implementations.windows.backend import (
            WindowsNotificationsBackend,
        )

        return WindowsNotificationsBackend()

    def powerprofiles(self) -> PowerProfilesControlBackend:
        from docking.applets.powerprofiles.implementations.windows.backend import (
            WindowsPowerProfilesBackend,
        )

        return WindowsPowerProfilesBackend()

    def screenshot(self) -> ScreenshotBackend:
        from docking.applets.screenshot.implementations.windows.backend import (
            WindowsScreenshotAPI,
            WindowsScreenshotBackend,
        )
        from docking.platform.backends.windows.windows import Win32WindowAPI

        return WindowsScreenshotBackend(
            api_factory=lambda: WindowsScreenshotAPI(
                window_source_factory=Win32WindowAPI
            )
        )

    def micshield(self) -> MicShieldBackend:
        from docking.applets.micshield.implementations.windows.backend import (
            WindowsMicShieldBackend,
        )

        return WindowsMicShieldBackend()

    def session(self) -> SessionActionBackend:
        from docking.applets.session.implementations.windows.backend import (
            WindowsSessionBackend,
        )

        return WindowsSessionBackend()

    def trash(self) -> TrashBackend:
        from docking.applets.trash.implementations.windows.backend import (
            WindowsTrashBackend,
        )

        return WindowsTrashBackend()

    def gpu(self) -> GpuSource:
        from docking.applets.systemmonitor.implementations.windows.backend import (
            UnavailableGpuSource,
        )

        return UnavailableGpuSource()

    def browser(self) -> BrowserFactory:
        if os.environ.get("DOCKING_WEBENGINE_PYTHON"):
            from docking.applets.webapp.implementations.windows.companion import browser

            return browser.WindowsCompanionBrowser
        from docking.applets.webapp.implementations.windows.backend import (
            WindowsWebAppBrowser,
        )

        return WindowsWebAppBrowser

    def tray_presentation(
        self,
        *,
        icon_size: int,
        changed: Callable[[], None],
        state: Callable[[], TrayState],
        anchor: Callable[[], PopupAnchor | None],
        refresh: Callable[[], None],
    ) -> TrayPresentation:
        from docking.applets.systemtray.implementations.windows.presentation import (
            WindowsTrayPresentation,
        )

        return WindowsTrayPresentation()


class WindowsPlatformProvider(PlatformProvider):
    def usage_hooks(self) -> UsageHooks:
        from docking.applets.aiusage.implementations.windows.hooks import (
            WindowsUsageHooks,
        )

        return WindowsUsageHooks(
            host_services(),
            root=Path(__file__).resolve().parents[3],
            executable=sys.executable,
        )

    def applet_backends(self) -> AppletBackendFactories:
        return WindowsAppletBackendFactories()

    def application_sources(self) -> ApplicationSources:
        from docking.platform.applications.backend import ApplicationSources
        from docking.platform.applications.implementations.windows.associations import (
            WindowsApplicationAssociationBackend,
        )
        from docking.platform.applications.implementations.windows.catalog import (
            WindowsApplicationCatalogBackend,
        )
        from docking.platform.applications.implementations.windows.registration import (
            WindowsExecutableRegistration,
        )

        registrations = WindowsExecutableRegistration()
        catalog = WindowsApplicationCatalogBackend(registrations)
        return ApplicationSources(
            catalog, WindowsApplicationAssociationBackend(catalog.lookup), registrations
        )

    def launches(self) -> ApplicationLaunchBackend:
        from docking.platform.applications.implementations.windows.launch import (
            WindowsApplicationLaunchBackend,
        )

        return WindowsApplicationLaunchBackend()

    def processes(self) -> ProcessBackend:
        from docking.platform.applications.implementations.windows.process import (
            WindowsProcessBackend,
        )

        return WindowsProcessBackend()

    def desktop(self) -> DesktopServices:
        from docking.platform.desktop.implementations.windows.backend import (
            WindowsDesktopServices,
        )

        return WindowsDesktopServices()

    def tray_backend(self) -> TrayBackend:
        from docking.platform.tray.implementations.windows.backend import (
            WindowsTrayBackend,
        )

        return WindowsTrayBackend()

    def search(self) -> SearchIntegration:
        from docking.search.services.implementations.windows.platform import (
            WindowsSearchIntegration,
        )

        return WindowsSearchIntegration()

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

    def camera_helper(self, argv: list[str] | None = None) -> int:
        from docking.applets.camshield.implementations.windows.helper import main

        return main(argv)
