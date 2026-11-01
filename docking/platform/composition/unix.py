"""Factories for protocols shared by Linux and BSD."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from docking.applets.platform import AppletBackendFactories
from docking.platform.composition.base import PlatformProvider
from docking.platform.host.selection import host_services

if TYPE_CHECKING:
    from docking.applets.aiusage.hooks import UsageHooks
    from docking.applets.caffeine.inhibit import Inhibitor
    from docking.applets.capslock.backend import LockKeyBackend
    from docking.applets.keyboardlayout.backend import LayoutBackend
    from docking.applets.micshield.backend import MicShieldBackend
    from docking.applets.music.backend import MusicBackend
    from docking.applets.notifications.backend import NotificationsBackend
    from docking.applets.popup import PopupAnchor
    from docking.applets.powerprofiles.backend import PowerProfilesControlBackend
    from docking.applets.screenshot.backend import ScreenshotBackend
    from docking.applets.systemtray.presentation import TrayPresentation
    from docking.applets.trash.backend import TrashBackend
    from docking.applets.webapp.backend import BrowserFactory
    from docking.core.config import Config
    from docking.platform.applications.backend import (
        ApplicationLaunchBackend,
        ApplicationSources,
    )
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.base import SessionBackend
    from docking.platform.desktop.base import DesktopServices
    from docking.platform.model import DockModel
    from docking.platform.tray.backend import TrayBackend
    from docking.platform.tray.state import TrayState
    from docking.search.services.platform import SearchIntegration


class UnixAppletBackendFactories(AppletBackendFactories):
    def capslock(self) -> LockKeyBackend:
        from docking.applets.capslock.implementations.unix.backend import (
            X11LockKeyBackend,
        )

        return X11LockKeyBackend()

    def keyboardlayout(self) -> LayoutBackend:
        from docking.applets.keyboardlayout.implementations.unix.backend import (
            LinuxKeyboardBackend,
        )

        return LinuxKeyboardBackend()

    def notifications(self) -> NotificationsBackend:
        from docking.applets.notifications.implementations.unix.backend import (
            LinuxNotificationsBackend,
        )

        return LinuxNotificationsBackend()

    def powerprofiles(self) -> PowerProfilesControlBackend:
        from docking.applets.powerprofiles.implementations.unix.backend import (
            detect_backend,
        )

        return detect_backend()

    def screenshot(self) -> ScreenshotBackend:
        from docking.applets.screenshot.implementations.unix.backend import (
            UnixScreenshotBackend,
        )

        return UnixScreenshotBackend()

    def micshield(self) -> MicShieldBackend:
        from docking.applets.micshield.implementations.unix.backend import (
            LinuxMicShieldBackend,
        )

        return LinuxMicShieldBackend()

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
        self,
        *,
        icon_size: int,
        changed: Callable[[], None],
        state: Callable[[], TrayState],
        anchor: Callable[[], PopupAnchor | None],
        refresh: Callable[[], None],
    ) -> TrayPresentation:
        from docking.applets.systemtray.implementations.unix.presentation import (
            UnixTrayPresentation,
        )

        return UnixTrayPresentation(
            icon_size=icon_size,
            changed=changed,
            state=state,
            anchor=anchor,
            refresh=refresh,
        )


class UnixPlatformProvider(PlatformProvider):
    def usage_hooks(self) -> UsageHooks:
        from docking.applets.aiusage.implementations.unix.hooks import UnixUsageHooks

        return UnixUsageHooks(
            host_services(),
            root=Path(__file__).resolve().parents[3],
            executable=sys.executable,
        )

    def application_sources(self) -> ApplicationSources:
        from docking.platform.applications.backend import ApplicationSources
        from docking.platform.applications.implementations.unix.associations import (
            UnixApplicationAssociationBackend,
        )
        from docking.platform.applications.implementations.unix.catalog import (
            UnixApplicationCatalogBackend,
        )
        from docking.platform.applications.implementations.unix.registration import (
            UnixExecutableRegistrationBackend,
        )
        from docking.platform.applications.implementations.unix.steam import (
            SteamDiscovery,
        )

        return ApplicationSources(
            catalog=UnixApplicationCatalogBackend(steam_source=SteamDiscovery()),
            associations=UnixApplicationAssociationBackend(),
            registrations=UnixExecutableRegistrationBackend(),
        )

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
