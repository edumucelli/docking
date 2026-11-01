"""Host-specific composition of existing native adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .unix import UnixAppletBackendFactories, UnixPlatformProvider

if TYPE_CHECKING:
    from docking.applets.battery.backend import BatteryBackend
    from docking.applets.bluetooth.backend import BluetoothBackend
    from docking.applets.brightness.backend import BrightnessBackend
    from docking.applets.camshield.backend import CamshieldBackend
    from docking.applets.network.backend import NetworkBackend
    from docking.applets.session.backend import SessionBackend
    from docking.applets.systemmonitor.backend import SystemMonitorBackend
    from docking.applets.systemmonitor.gpu import GpuSource
    from docking.applets.thermals.backend import ThermalsBackend
    from docking.applets.volume.backend import VolumeBackend
    from docking.platform.applications.backend import ProcessBackend


class LinuxAppletBackendFactories(UnixAppletBackendFactories):
    def brightness(self) -> BrightnessBackend:
        from docking.applets.brightness.implementations.linux.backlight import (
            LinuxBacklightSource,
        )
        from docking.applets.brightness.implementations.unix.backend import (
            UnixBrightnessBackend,
        )

        return UnixBrightnessBackend(LinuxBacklightSource())

    def battery(self) -> BatteryBackend:
        from docking.applets.battery.implementations.linux.backend import (
            LinuxBatteryBackend,
        )

        return LinuxBatteryBackend()

    def bluetooth(self) -> BluetoothBackend:
        from docking.applets.bluetooth.implementations.linux.backend import (
            LinuxBluetoothBackend,
        )

        return LinuxBluetoothBackend()

    def camshield(self) -> CamshieldBackend:
        from docking.applets.camshield.implementations.linux.backend import (
            LinuxCamshieldBackend,
        )
        from docking.applets.camshield.implementations.unix.client import (
            UnixCamshieldClient,
        )

        return UnixCamshieldClient(LinuxCamshieldBackend())

    def network(self, interface: str = "") -> NetworkBackend:
        from docking.applets.network.implementations.linux.backend import (
            LinuxNetworkBackend,
        )

        return LinuxNetworkBackend(interface)

    def session(self) -> SessionBackend:
        from docking.applets.session.implementations.linux.backend import (
            LinuxSessionBackend,
        )

        return LinuxSessionBackend()

    def systemmonitor(self) -> SystemMonitorBackend:
        from docking.applets.systemmonitor.implementations.linux.backend import (
            LinuxSystemMonitorBackend,
        )

        return LinuxSystemMonitorBackend()

    def thermals(self) -> ThermalsBackend:
        from docking.applets.thermals.implementations.linux.backend import (
            LinuxThermalsBackend,
        )

        return LinuxThermalsBackend()

    def volume(self) -> VolumeBackend | None:
        from docking.applets.volume.implementations.linux.backend import create_backend

        return create_backend()

    def gpu(self) -> GpuSource:
        from docking.applets.systemmonitor.implementations.linux.gpu import GpuReader

        return GpuReader()


class LinuxPlatformProvider(UnixPlatformProvider):
    def applet_backends(self) -> LinuxAppletBackendFactories:
        return LinuxAppletBackendFactories()

    def processes(self) -> ProcessBackend:
        from docking.platform.applications.implementations.linux.process import (
            LinuxProcessBackend,
        )

        return LinuxProcessBackend()

    def camera_helper(self, argv: list[str] | None = None) -> int:
        from docking.applets.camshield.implementations.linux.backend import (
            LinuxCamshieldBackend,
        )
        from docking.applets.camshield.implementations.unix.helper import main

        return main(argv, backend=LinuxCamshieldBackend())
