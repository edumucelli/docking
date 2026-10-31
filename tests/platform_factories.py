"""Explicit Linux/Unix test composition without a running desktop graph."""

from __future__ import annotations

from pathlib import Path

from docking.applets.platform import AppletPlatformServices
from docking.platform.applications.identity import ProcessIdentityService
from docking.platform.applications.implementations.linux.process import (
    LinuxProcessBackend,
)
from docking.platform.applications.implementations.unix.associations import (
    UnixApplicationAssociationBackend,
)
from docking.platform.applications.implementations.unix.catalog import (
    UnixApplicationCatalogBackend,
)
from docking.platform.applications.implementations.unix.launch import (
    UnixApplicationLaunchBackend,
)
from docking.platform.applications.implementations.unix.registration import (
    UnixExecutableRegistrationBackend,
)
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.composition.linux import LinuxAppletBackendFactories
from docking.platform.model import DockModel
from docking.platform.targets import TargetService
from docking.platform.tray.backend import TrayBackend
from docking.platform.tray.service import TrayService
from docking.platform.tray.state import unavailable_state


class EmptyTrayBackend(TrayBackend):
    def initial_state(self):
        return unavailable_state()

    def close(self):
        pass

    def get_state(self):
        return unavailable_state()

    def activate(self, identifier):
        return False

    def context_menu(self, identifier):
        return False

    def menu_client(self, identifier):
        return None


def make_applet_platform(*, backends=None, tray=None):
    return AppletPlatformServices(
        TestAppletBackendFactories() if backends is None else backends,
        TestAppletBackendFactories.tray_service() if tray is None else tray,
    )


def make_application_registry(**kwargs):
    catalog = kwargs.pop("catalog_backend", None)
    associations = kwargs.pop("association_backend", None)
    registrations = kwargs.pop("registration_backend", None)
    return ApplicationRegistry(
        catalog_backend=catalog
        if catalog is not None
        else UnixApplicationCatalogBackend(**kwargs),
        association_backend=associations
        if associations is not None
        else UnixApplicationAssociationBackend(),
        registration_backend=registrations
        if registrations is not None
        else UnixExecutableRegistrationBackend(),
    )


def make_application_launcher(registry, provenance_store, **kwargs):
    backend = kwargs.pop("backend", None)
    return ApplicationLauncher(
        registry,
        provenance_store,
        backend=backend
        if backend is not None
        else UnixApplicationLaunchBackend(**kwargs),
    )


def make_process_identity(
    provenance_store, *, proc_root=Path("/proc"), backend=None, **kwargs
):
    return ProcessIdentityService(
        provenance_store,
        backend=backend if backend is not None else LinuxProcessBackend(proc_root),
        **kwargs,
    )


def make_target_service(*, icon_loader, application_registry=None):
    registry = (
        application_registry
        if application_registry is not None
        else make_application_registry(
            application_source=lambda: (), desktop_directories_source=lambda: ()
        )
    )
    return TargetService(icon_loader=icon_loader, application_registry=registry)


def make_dock_model(**kwargs):
    kwargs.setdefault("applet_platform", make_applet_platform())
    return DockModel(**kwargs)


def probe_camera_state(*, dev_root=Path("/dev"), proc_root=Path("/proc")):
    from docking.applets.camshield.implementations.linux.backend import (
        _probe_camera_state,
    )

    return _probe_camera_state(dev_root=dev_root, proc_root=proc_root)


def read_thermal_snapshot(**kwargs):
    from docking.applets.thermals.implementations.linux.backend import (
        LinuxThermalsBackend,
    )

    return LinuxThermalsBackend(**kwargs).get_snapshot()


def lock_screen():
    from docking.applets.session.implementations.linux.backend import (
        LinuxSessionBackend,
    )

    return LinuxSessionBackend().perform("lock")


def make_telegram_browser(**callbacks):
    from docking.applets.telegram.state import PROFILE
    from docking.applets.webapp.implementations.unix.backend import WebAppBrowser

    return WebAppBrowser(profile=PROFILE, **callbacks)


def make_whatsapp_browser(**callbacks):
    from docking.applets.webapp.implementations.unix.backend import WebAppBrowser
    from docking.applets.whatsapp.state import PROFILE

    return WebAppBrowser(profile=PROFILE, **callbacks)


class TestAppletBackendFactories(LinuxAppletBackendFactories):
    __test__ = False

    @staticmethod
    def tray_service():
        return TrayService(backend_factory=EmptyTrayBackend)


def camera_control():
    from docking.applets.camshield.implementations.linux.backend import (
        LinuxCamshieldBackend,
    )

    return LinuxCamshieldBackend()


class OwnedTestWorker:
    def __init__(self, worker, on_close):
        self.worker = worker
        self.on_close = on_close
        self.closed = False

    def run_guarded(self, **kwargs):
        return self.worker.run_guarded(**kwargs)

    def stop(self):
        if not self.closed:
            self.closed = True
            self.on_close()


def make_tray_service(*, backend=None, worker=None, backend_factory=None, interval_s=3):
    from docking.applets.worker import BackgroundWorker

    factory = backend_factory if backend_factory is not None else lambda: backend
    owner = BackgroundWorker() if worker is None else worker
    return TrayService(
        backend_factory=factory,
        interval_s=interval_s,
        worker_factory=lambda *, on_close, logger: OwnedTestWorker(owner, on_close),
    )
