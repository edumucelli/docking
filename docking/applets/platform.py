"""Stable construction dependencies, separate from late session/UI services."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
    from docking.applets.session.backend import SessionBackend
    from docking.applets.systemmonitor.backend import SystemMonitorBackend
    from docking.applets.systemmonitor.gpu import GpuSource
    from docking.applets.systemtray.presentation import TrayPresentation
    from docking.applets.thermals.backend import ThermalsBackend
    from docking.applets.trash.backend import TrashBackend
    from docking.applets.volume.backend import VolumeBackend
    from docking.applets.webapp.backend import BrowserFactory
    from docking.platform.tray.service import TrayService
    from docking.platform.tray.state import TrayState


class AppletBackendFactories(ABC):
    @abstractmethod
    def battery(self) -> BatteryBackend: ...
    @abstractmethod
    def bluetooth(self) -> BluetoothBackend: ...
    @abstractmethod
    def camshield(self) -> CamshieldBackend: ...
    @abstractmethod
    def network(self, interface: str = "") -> NetworkBackend: ...
    @abstractmethod
    def volume(self) -> VolumeBackend | None: ...
    @abstractmethod
    def systemmonitor(self) -> SystemMonitorBackend: ...
    @abstractmethod
    def thermals(self) -> ThermalsBackend: ...
    @abstractmethod
    def caffeine(self) -> Inhibitor: ...
    @abstractmethod
    def music(self) -> MusicBackend: ...
    @abstractmethod
    def capslock(self) -> LockKeyBackend: ...
    @abstractmethod
    def brightness(self) -> BrightnessBackend: ...
    @abstractmethod
    def keyboardlayout(self) -> LayoutBackend: ...
    @abstractmethod
    def notifications(self) -> NotificationsBackend: ...
    @abstractmethod
    def powerprofiles(self) -> PowerProfilesControlBackend: ...
    @abstractmethod
    def screenshot(self) -> ScreenshotBackend: ...
    @abstractmethod
    def micshield(self) -> MicShieldBackend: ...
    @abstractmethod
    def session(self) -> SessionBackend: ...
    @abstractmethod
    def gpu(self) -> GpuSource: ...
    @abstractmethod
    def trash(self) -> TrashBackend: ...
    @abstractmethod
    def browser(self) -> BrowserFactory: ...
    @abstractmethod
    def tray_presentation(
        self,
        *,
        icon_size: int,
        changed: Callable[[], None],
        state: Callable[[], TrayState],
        anchor: Callable[[], PopupAnchor | None],
        refresh: Callable[[], None],
    ) -> TrayPresentation: ...


@dataclass(frozen=True, slots=True)
class AppletPlatformServices:
    backends: AppletBackendFactories
    tray: TrayService
