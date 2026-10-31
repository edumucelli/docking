"""Stable construction dependencies, separate from late session/UI services."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.applets.battery.backend import BatteryBackend
    from docking.applets.bluetooth.backend import BluetoothBackend
    from docking.applets.bluetooth.operations import BluetoothOperations
    from docking.applets.brightness.backend import BrightnessOperations
    from docking.applets.caffeine.inhibit import Inhibitor
    from docking.applets.camshield.backend import CamshieldBackend
    from docking.applets.capslock.backend import CapslockOperations
    from docking.applets.keyboardlayout.backend import KeyboardlayoutOperations
    from docking.applets.micshield.backend import MicshieldOperations
    from docking.applets.music.backend import MusicBackend
    from docking.applets.network.backend import NetworkBackend
    from docking.applets.network.operations import NetworkOperations
    from docking.applets.notifications.backend import NotificationsOperations
    from docking.applets.powerprofiles.backend import PowerprofilesOperations
    from docking.applets.screenshot.backend import ScreenshotOperations
    from docking.applets.session.backend import SessionBackend
    from docking.applets.systemmonitor.backend import SystemMonitorBackend
    from docking.applets.systemmonitor.gpu import GpuSource
    from docking.applets.systemtray.presentation import TrayPresentation
    from docking.applets.thermals.backend import ThermalsBackend
    from docking.applets.trash.backend import TrashBackend
    from docking.applets.volume.backend import VolumeBackend
    from docking.applets.volume.operations import VolumeOperations
    from docking.applets.webapp.backend import BrowserFactory
    from docking.platform.tray.service import TrayService


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
    def capslock(self) -> CapslockOperations: ...
    @abstractmethod
    def brightness(self) -> BrightnessOperations: ...
    @abstractmethod
    def keyboardlayout(self) -> KeyboardlayoutOperations: ...
    @abstractmethod
    def notifications(self) -> NotificationsOperations: ...
    @abstractmethod
    def powerprofiles(self) -> PowerprofilesOperations: ...
    @abstractmethod
    def screenshot(self) -> ScreenshotOperations: ...
    @abstractmethod
    def bluetooth_desktop(self) -> BluetoothOperations: ...
    @abstractmethod
    def network_settings(self) -> NetworkOperations: ...
    @abstractmethod
    def volume_settings(self) -> VolumeOperations: ...
    @abstractmethod
    def micshield(self) -> MicshieldOperations: ...
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
        self, *, icon_size: int, on_changed: Callable[[], None]
    ) -> TrayPresentation: ...


@dataclass(frozen=True, slots=True)
class AppletPlatformServices:
    backends: AppletBackendFactories
    tray: TrayService
