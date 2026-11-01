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


class BsdAppletBackendFactories(UnixAppletBackendFactories):
    def brightness(self) -> BrightnessBackend:
        from docking.applets.brightness.implementations.unix.backend import (
            NoBacklightSource,
            UnixBrightnessBackend,
        )

        return UnixBrightnessBackend(NoBacklightSource())

    def battery(self) -> BatteryBackend:
        from docking.applets.battery.implementations.bsd.backend import (
            BsdBatteryBackend,
        )

        return BsdBatteryBackend()

    def bluetooth(self) -> BluetoothBackend:
        from docking.applets.bluetooth.implementations.bsd.backend import (
            BsdBluetoothBackend,
        )

        return BsdBluetoothBackend()

    def camshield(self) -> CamshieldBackend:
        from docking.applets.camshield.implementations.bsd.backend import (
            BsdCamshieldBackend,
        )
        from docking.applets.camshield.implementations.unix.client import (
            UnixCamshieldClient,
        )

        return UnixCamshieldClient(BsdCamshieldBackend())

    def network(self, interface: str = "") -> NetworkBackend:
        from docking.applets.network.implementations.bsd.backend import (
            BsdNetworkBackend,
        )

        return BsdNetworkBackend(interface)

    def session(self) -> SessionBackend:
        from docking.applets.session.implementations.bsd.backend import (
            BsdSessionBackend,
        )

        return BsdSessionBackend()

    def systemmonitor(self) -> SystemMonitorBackend:
        from docking.applets.systemmonitor.implementations.bsd.backend import (
            BsdSystemMonitorBackend,
        )

        return BsdSystemMonitorBackend()

    def thermals(self) -> ThermalsBackend:
        from docking.applets.thermals.implementations.bsd.backend import (
            BsdThermalsBackend,
        )

        return BsdThermalsBackend()

    def volume(self) -> VolumeBackend | None:
        from docking.applets.volume.implementations.bsd.backend import create_backend

        return create_backend()

    def gpu(self) -> GpuSource:
        from docking.applets.systemmonitor.implementations.shared.gpu import (
            VendorGpuReader,
        )

        return VendorGpuReader()


class BsdPlatformProvider(UnixPlatformProvider):
    def applet_backends(self) -> BsdAppletBackendFactories:
        return BsdAppletBackendFactories()

    def processes(self) -> ProcessBackend:
        from docking.platform.applications.implementations.bsd.process import (
            BsdProcessBackend,
        )

        return BsdProcessBackend()

    def camera_helper(self, argv: list[str] | None = None) -> int:
        from docking.applets.camshield.implementations.bsd.backend import (
            BsdCamshieldBackend,
        )
        from docking.applets.camshield.implementations.unix.helper import main

        return main(argv, backend=BsdCamshieldBackend())
