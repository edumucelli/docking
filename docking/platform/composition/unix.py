"""Factories for protocols shared by Linux and BSD."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from docking.applets.platform import AppletBackendFactories
from docking.platform.composition.base import PlatformProvider

if TYPE_CHECKING:
    from docking.applets.bluetooth.operations import BluetoothOperations
    from docking.applets.caffeine.inhibit import Inhibitor
    from docking.applets.capslock.backend import CapslockOperations
    from docking.applets.keyboardlayout.backend import KeyboardlayoutOperations
    from docking.applets.micshield.backend import MicshieldOperations
    from docking.applets.music.backend import MusicBackend
    from docking.applets.network.operations import NetworkOperations
    from docking.applets.notifications.backend import NotificationsOperations
    from docking.applets.powerprofiles.backend import PowerprofilesOperations
    from docking.applets.screenshot.backend import ScreenshotOperations
    from docking.applets.systemtray.presentation import TrayPresentation
    from docking.applets.trash.backend import TrashBackend
    from docking.applets.volume.operations import VolumeOperations
    from docking.applets.webapp.backend import BrowserFactory
    from docking.core.config import Config
    from docking.platform.applications.backend import (
        ApplicationAssociationBackend,
        ApplicationCatalogBackend,
        ApplicationLaunchBackend,
        ExecutableRegistrationBackend,
    )
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.base import SessionBackend
    from docking.platform.desktop.base import DesktopServices
    from docking.platform.model import DockModel
    from docking.platform.tray.backend import TrayBackend
    from docking.search.services.platform import SearchIntegration


class UnixAppletBackendFactories(AppletBackendFactories):
    def capslock(self) -> CapslockOperations:
        from docking.applets.capslock.implementations.unix.backend import (
            CapslockOperations,
        )

        return CapslockOperations()

    def keyboardlayout(self) -> KeyboardlayoutOperations:
        from docking.applets.keyboardlayout.implementations.unix.backend import (
            KeyboardlayoutOperations,
        )

        return KeyboardlayoutOperations()

    def notifications(self) -> NotificationsOperations:
        from docking.applets.notifications.implementations.unix.backend import (
            NotificationsOperations,
        )

        return NotificationsOperations()

    def powerprofiles(self) -> PowerprofilesOperations:
        from docking.applets.powerprofiles.implementations.unix.backend import (
            PowerprofilesOperations,
        )

        return PowerprofilesOperations()

    def screenshot(self) -> ScreenshotOperations:
        from docking.applets.screenshot.implementations.unix.backend import (
            ScreenshotOperations,
        )

        return ScreenshotOperations()

    def bluetooth_desktop(self) -> BluetoothOperations:
        from docking.applets.bluetooth.implementations.unix.desktop import (
            BluetoothOperations,
        )

        return BluetoothOperations()

    def network_settings(self) -> NetworkOperations:
        from docking.applets.network.implementations.unix.desktop_settings import (
            NetworkOperations,
        )

        return NetworkOperations()

    def volume_settings(self) -> VolumeOperations:
        from docking.applets.volume.implementations.unix.desktop_settings import (
            VolumeOperations,
        )

        return VolumeOperations()

    def micshield(self) -> MicshieldOperations:
        from docking.applets.micshield.implementations.unix.backend import (
            MicshieldOperations,
        )

        return MicshieldOperations()

    def caffeine(self) -> Inhibitor:
        from docking.applets.caffeine.implementations.unix.backend import (
            default_inhibitor,
        )

        return default_inhibitor()

    def music(self) -> MusicBackend:
        from docking.applets.music.implementations.unix.backend import HybridBackend

        return HybridBackend()

    def trash(self) -> TrashBackend:
        from docking.applets.trash.implementations.unix.backend import (
            select_trash_backend,
        )

        return select_trash_backend()

    def browser(self) -> BrowserFactory:
        from docking.applets.webapp.implementations.unix.backend import WebAppBrowser

        return WebAppBrowser

    def tray_presentation(
        self, *, icon_size: int, on_changed: Callable[[], None]
    ) -> TrayPresentation:
        from docking.applets.systemtray.implementations.unix.presentation import (
            UnixTrayPresentation,
        )

        return UnixTrayPresentation(icon_size=icon_size, on_changed=on_changed)


class UnixPlatformProvider(PlatformProvider):
    def catalog(self) -> ApplicationCatalogBackend:
        from docking.platform.applications.implementations.unix.catalog import (
            UnixApplicationCatalogBackend,
        )
        from docking.platform.applications.implementations.unix.steam import (
            SteamDiscovery,
        )

        return UnixApplicationCatalogBackend(steam_source=SteamDiscovery())

    def associations(self) -> ApplicationAssociationBackend:
        from docking.platform.applications.implementations.unix.associations import (
            UnixApplicationAssociationBackend,
        )

        return UnixApplicationAssociationBackend()

    def registrations(self) -> ExecutableRegistrationBackend:
        from docking.platform.applications.implementations.unix.registration import (
            UnixExecutableRegistrationBackend,
        )

        return UnixExecutableRegistrationBackend()

    def launches(self) -> ApplicationLaunchBackend:
        from docking.platform.applications.implementations.unix.launch import (
            UnixApplicationLaunchBackend,
        )

        return UnixApplicationLaunchBackend()

    def desktop(self) -> DesktopServices:
        from docking.platform.desktop.selection import desktop_services

        return desktop_services()

    def tray_backend(self) -> TrayBackend:
        from docking.platform.tray.implementations.unix.backend import UnixTrayBackend

        return UnixTrayBackend()

    def search(self) -> SearchIntegration:
        from docking.search.services.implementations.unix.platform import (
            UnixSearchIntegration,
        )

        return UnixSearchIntegration()

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
