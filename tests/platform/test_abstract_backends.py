"""Owned platform adapters must implement their nominal contracts completely."""

from __future__ import annotations

import inspect
from abc import ABCMeta
from importlib import import_module

import pytest

CONTRACTS = (
    "docking.platform.applications.backend.CatalogMonitor",
    "docking.platform.applications.backend.ApplicationCatalogBackend",
    "docking.platform.applications.backend.ApplicationAssociationBackend",
    "docking.platform.applications.backend.ApplicationLaunchBackend",
    "docking.platform.applications.backend.ExecutableRegistrationBackend",
    "docking.platform.composition.base.PlatformProvider",
    "docking.applets.platform.AppletBackendFactories",
    "docking.platform.host.base.HostServices",
    "docking.platform.desktop.base.DesktopServices",
    "docking.platform.tray.backend.TrayBackend",
    "docking.platform.tray.menu.TrayMenuClient",
    "docking.applets.systemtray.presentation.TrayPresentation",
    "docking.search.services.platform.ShortcutFallback",
    "docking.search.services.platform.SearchIntegration",
    "docking.search.services.global_shortcuts.GlobalShortcutService",
    "docking.search.services.implementations.unix.x11_shortcuts.ShortcutWorker",
    "docking.applets.brightness.backend.BacklightSource",
    "docking.applets.brightness.backend.BrightnessOperations",
    "docking.applets.bluetooth.operations.BluetoothOperations",
    "docking.applets.camshield.backend.CamshieldBackend",
    "docking.applets.capslock.backend.CapslockOperations",
    "docking.applets.keyboardlayout.backend.KeyboardlayoutOperations",
    "docking.applets.micshield.backend.MicshieldOperations",
    "docking.applets.music.backend.MusicBackend",
    "docking.applets.network.operations.NetworkOperations",
    "docking.applets.notifications.backend.NotificationActivityMonitor",
    "docking.applets.notifications.backend.NotificationsOperations",
    "docking.applets.notifications.backend.NotificationsBackend",
    "docking.applets.powerprofiles.backend.PowerprofilesOperations",
    "docking.applets.powerprofiles.backend.PowerProfilesControlBackend",
    "docking.applets.screenshot.backend.ScreenshotOperations",
    "docking.applets.volume.operations.VolumeOperations",
    "docking.applets.trash.backend.TrashBackend",
    "docking.applets.webapp.backend.BrowserBackend",
    "docking.applets.systemmonitor.gpu.GpuSource",
    "docking.applets.caffeine.inhibit.Inhibitor",
    "docking.applets.session.implementations.unix.actions.SessionCommands",
    "docking.applets.thermals.backend.ThermalsBackend",
)

ADAPTERS = (
    (
        "docking.platform.composition.unix.UnixPlatformProvider",
        "docking.platform.composition.base.PlatformProvider",
        True,
    ),
    (
        "docking.platform.composition.unix.UnixAppletBackendFactories",
        "docking.applets.platform.AppletBackendFactories",
        True,
    ),
    (
        "docking.platform.host.implementations.unix.backend.UnixHostServices",
        "docking.platform.host.base.HostServices",
        True,
    ),
    (
        "docking.platform.desktop.implementations.unix.backend.UnixDesktopServices",
        "docking.platform.desktop.base.DesktopServices",
        False,
    ),
    (
        "docking.platform.applications.implementations.unix.catalog.UnixApplicationCatalogBackend",
        "docking.platform.applications.backend.ApplicationCatalogBackend",
        False,
    ),
    (
        "docking.platform.applications.implementations.unix.monitor.UnixCatalogMonitor",
        "docking.platform.applications.backend.CatalogMonitor",
        False,
    ),
    (
        "docking.platform.applications.implementations.unix.registration.UnixExecutableRegistrationBackend",
        "docking.platform.applications.backend.ExecutableRegistrationBackend",
        False,
    ),
    (
        "docking.platform.applications.implementations.unix.associations.UnixApplicationAssociationBackend",
        "docking.platform.applications.backend.ApplicationAssociationBackend",
        False,
    ),
    (
        "docking.platform.applications.implementations.unix.launch.UnixApplicationLaunchBackend",
        "docking.platform.applications.backend.ApplicationLaunchBackend",
        False,
    ),
    (
        "docking.platform.tray.implementations.unix.backend.UnixTrayBackend",
        "docking.platform.tray.backend.TrayBackend",
        False,
    ),
    (
        "docking.platform.tray.implementations.unix.dbusmenu.DBusMenuClient",
        "docking.platform.tray.menu.TrayMenuClient",
        False,
    ),
    (
        "docking.applets.systemtray.implementations.unix.presentation.UnixTrayPresentation",
        "docking.applets.systemtray.presentation.TrayPresentation",
        False,
    ),
    (
        "docking.search.services.implementations.unix.platform.UnixSearchIntegration",
        "docking.search.services.platform.SearchIntegration",
        False,
    ),
    (
        "docking.search.services.implementations.unix.shortcuts.GlobalShortcutsService",
        "docking.search.services.global_shortcuts.GlobalShortcutService",
        False,
    ),
    (
        "docking.search.services.implementations.unix.x11_shortcuts.X11GlobalShortcutService",
        "docking.search.services.platform.ShortcutFallback",
        False,
    ),
    (
        "docking.search.services.implementations.unix.x11_shortcuts._ProcessShortcutWorker",
        "docking.search.services.implementations.unix.x11_shortcuts.ShortcutWorker",
        False,
    ),
    (
        "docking.applets.music.implementations.unix.backend.HybridBackend",
        "docking.applets.music.backend.MusicBackend",
        False,
    ),
    (
        "docking.applets.webapp.implementations.unix.backend.WebAppBrowser",
        "docking.applets.webapp.backend.BrowserBackend",
        False,
    ),
    (
        "docking.applets.camshield.implementations.unix.client.UnixCamshieldClient",
        "docking.applets.camshield.backend.CamshieldBackend",
        False,
    ),
    (
        "docking.applets.notifications.implementations.unix.activity.UnixNotificationActivityMonitor",
        "docking.applets.notifications.backend.NotificationActivityMonitor",
        False,
    ),
    (
        "docking.applets.notifications.implementations.unix.backend.DunstBackend",
        "docking.applets.notifications.backend.NotificationsBackend",
        False,
    ),
    (
        "docking.applets.notifications.implementations.unix.backend.GnomeBackend",
        "docking.applets.notifications.backend.NotificationsBackend",
        False,
    ),
    (
        "docking.applets.notifications.implementations.unix.backend.NullBackend",
        "docking.applets.notifications.backend.NotificationsBackend",
        False,
    ),
    (
        "docking.applets.powerprofiles.implementations.unix.backend.NullPowerProfilesBackend",
        "docking.applets.powerprofiles.backend.PowerProfilesControlBackend",
        False,
    ),
    (
        "docking.applets.powerprofiles.implementations.unix.backend.PowerProfilesBackend",
        "docking.applets.powerprofiles.backend.PowerProfilesControlBackend",
        False,
    ),
    (
        "docking.applets.powerprofiles.implementations.unix.backend.TunedBackend",
        "docking.applets.powerprofiles.backend.PowerProfilesControlBackend",
        False,
    ),
    (
        "docking.applets.powerprofiles.implementations.unix.backend.TlpBackend",
        "docking.applets.powerprofiles.backend.PowerProfilesControlBackend",
        False,
    ),
    (
        "docking.applets.capslock.implementations.unix.backend.CapslockOperations",
        "docking.applets.capslock.backend.CapslockOperations",
        False,
    ),
    (
        "docking.applets.brightness.implementations.unix.backend.BrightnessOperations",
        "docking.applets.brightness.backend.BrightnessOperations",
        False,
    ),
    (
        "docking.applets.bluetooth.implementations.unix.desktop.BluetoothOperations",
        "docking.applets.bluetooth.operations.BluetoothOperations",
        False,
    ),
    (
        "docking.applets.keyboardlayout.implementations.unix.backend.KeyboardlayoutOperations",
        "docking.applets.keyboardlayout.backend.KeyboardlayoutOperations",
        False,
    ),
    (
        "docking.applets.micshield.implementations.unix.backend.MicshieldOperations",
        "docking.applets.micshield.backend.MicshieldOperations",
        False,
    ),
    (
        "docking.applets.network.implementations.unix.desktop_settings.NetworkOperations",
        "docking.applets.network.operations.NetworkOperations",
        False,
    ),
    (
        "docking.applets.volume.implementations.unix.desktop_settings.VolumeOperations",
        "docking.applets.volume.operations.VolumeOperations",
        False,
    ),
    (
        "docking.applets.notifications.implementations.unix.backend.NotificationsOperations",
        "docking.applets.notifications.backend.NotificationsOperations",
        False,
    ),
    (
        "docking.applets.powerprofiles.implementations.unix.backend.PowerprofilesOperations",
        "docking.applets.powerprofiles.backend.PowerprofilesOperations",
        False,
    ),
    (
        "docking.applets.screenshot.implementations.unix.backend.ScreenshotOperations",
        "docking.applets.screenshot.backend.ScreenshotOperations",
        False,
    ),
    (
        "docking.applets.brightness.implementations.linux.backlight.LinuxBacklightSource",
        "docking.applets.brightness.backend.BacklightSource",
        False,
    ),
    (
        "docking.applets.brightness.implementations.unix.backend.NoBacklightSource",
        "docking.applets.brightness.backend.BacklightSource",
        False,
    ),
    (
        "docking.applets.trash.implementations.unix.backend.GioTrashBackend",
        "docking.applets.trash.backend.TrashBackend",
        False,
    ),
    (
        "docking.applets.trash.implementations.unix.backend.KdeTrashBackend",
        "docking.applets.trash.backend.TrashBackend",
        False,
    ),
    (
        "docking.applets.systemmonitor.implementations.shared.gpu.VendorGpuReader",
        "docking.applets.systemmonitor.gpu.GpuSource",
        False,
    ),
    (
        "docking.applets.caffeine.inhibit.CompositeInhibitor",
        "docking.applets.caffeine.inhibit.Inhibitor",
        False,
    ),
    (
        "docking.applets.caffeine.implementations.unix.backend.ScreenSaverInhibitor",
        "docking.applets.caffeine.inhibit.Inhibitor",
        False,
    ),
    (
        "docking.applets.caffeine.implementations.unix.backend.SystemdSleepInhibitor",
        "docking.applets.caffeine.inhibit.Inhibitor",
        False,
    ),
    (
        "docking.applets.session.implementations.linux.backend.LinuxSessionBackend",
        "docking.applets.session.implementations.unix.actions.SessionCommands",
        False,
    ),
    (
        "docking.applets.session.implementations.bsd.backend.BsdSessionBackend",
        "docking.applets.session.implementations.unix.actions.SessionCommands",
        False,
    ),
    (
        "docking.platform.composition.linux.LinuxPlatformProvider",
        "docking.platform.composition.base.PlatformProvider",
        False,
    ),
    (
        "docking.platform.composition.linux.LinuxAppletBackendFactories",
        "docking.applets.platform.AppletBackendFactories",
        False,
    ),
    (
        "docking.platform.host.implementations.linux.backend.LinuxHostServices",
        "docking.platform.host.base.HostServices",
        False,
    ),
    (
        "docking.platform.composition.bsd.BsdPlatformProvider",
        "docking.platform.composition.base.PlatformProvider",
        False,
    ),
    (
        "docking.platform.composition.bsd.BsdAppletBackendFactories",
        "docking.applets.platform.AppletBackendFactories",
        False,
    ),
    (
        "docking.platform.host.implementations.bsd.backend.BsdHostServices",
        "docking.platform.host.base.HostServices",
        False,
    ),
)


def _class(qualified_name: str) -> type:
    module_name, _, name = qualified_name.rpartition(".")
    return vars(import_module(module_name))[name]


@pytest.mark.parametrize("qualified_name", CONTRACTS)
def test_contract_requires_every_declared_operation(qualified_name):
    contract = _class(qualified_name)
    assert isinstance(contract, ABCMeta)
    assert inspect.isabstract(contract)
    required = {
        name
        for name, value in vars(contract).items()
        if isinstance(value, property) or inspect.isfunction(value)
    }
    assert contract.__abstractmethods__ == required

    # Omit each operation separately so an otherwise complete implementation
    # also fails before its constructor can acquire any native resources.
    for missing in required:
        members = {
            name: property(lambda self: None)
            if isinstance(vars(contract)[name], property)
            else lambda *args, **kwargs: None
            for name in required - {missing}
        }
        incomplete = type("Incomplete", (contract,), members)
        with pytest.raises(TypeError, match=missing):
            incomplete()


@pytest.mark.parametrize("adapter_name,contract_name,partial", ADAPTERS)
def test_native_adapter_inherits_its_contract(adapter_name, contract_name, partial):
    adapter = _class(adapter_name)
    contract = _class(contract_name)
    assert issubclass(adapter, contract)
    assert inspect.isabstract(adapter) is partial
