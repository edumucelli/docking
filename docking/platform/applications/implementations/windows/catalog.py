"""Windows catalog policy and worker-owned native metadata construction."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger
from docking.platform.applications import handle_adapters as discovery
from docking.platform.applications.associations import AssociationKey
from docking.platform.applications.backend import (
    ApplicationCatalogBackend,
    CatalogMonitor,
    DiscoveryResult,
)
from docking.platform.applications.implementations.shared.monitor import (
    GioCatalogMonitor,
)
from docking.platform.applications.implementations.windows import (
    native_catalog as catalog,
)
from docking.platform.applications.implementations.windows.associations import (
    read_associations,
)
from docking.platform.applications.implementations.windows.catalog_monitor import (
    CatalogChangeMonitor,
)
from docking.platform.applications.implementations.windows.discovery import (
    discover_windows_applications,
    merge_shell_catalog,
)
from docking.platform.applications.implementations.windows.export import (
    WindowsApplicationExport,
)
from docking.platform.applications.implementations.windows.registration import (
    WindowsExecutableRegistration,
)
from docking.platform.applications.metadata import (
    ApplicationMetadata,
    ApplicationMetadataLookup,
    MetadataResolver,
)
from docking.platform.applications.steam import SteamDiscoveryResult, SteamGame
from docking.platform.applications.types import ApplicationDiagnostic, ApplicationInfo

log = get_logger("application_metadata")


def _read_gio_discovery() -> DiscoveryResult | None:
    """Resolve Win32 AppInfo's potentially rebuilding cache on the worker."""
    try:
        return discover_windows_applications(tuple(Gio.AppInfo.get_all()))
    except (GLib.Error, OSError) as exc:
        log.debug("Could not read Windows Gio application metadata: %s", exc)
        return None


class WindowsApplicationCatalogBackend(ApplicationCatalogBackend):
    def __init__(
        self,
        registrations: WindowsExecutableRegistration,
        *,
        application_source: Callable[[], Iterable[object]] | None = None,
    ) -> None:
        self._registrations = registrations
        self._application_source = application_source
        self._change_monitor: CatalogChangeMonitor | None = None
        self._changed: Callable[[], None] | None = None
        self._active = False
        self.lookup = ApplicationMetadataLookup(
            resolve_factory=self.metadata_resolver_factory(
                registrations, read_default_source=application_source is None
            ),
            on_changed=self._on_metadata_changed,
            schedule_idle=GLib.idle_add,
            remove_source=GLib.source_remove,
        )

    @property
    def initial_discovery_pending(self) -> bool:
        return self.lookup.applications is None or (
            self._application_source is None and self.lookup.source_discovery is None
        )

    def invalidate(self) -> None:
        self.lookup.invalidate()

    def discover(self, *, handle_epoch: int) -> DiscoveryResult:
        result = (
            discover_windows_applications(self._application_source())
            if self._application_source is not None
            else self.lookup.source_discovery
        )
        if result is None:
            result = DiscoveryResult((), {}, (), {}, ())
        if self.lookup.applications is not None:
            result = merge_shell_catalog(result, self.lookup.applications)
        registered = list(result.applications)
        diagnostics = list(result.diagnostics)
        order = list(result.presentation_order)
        known = {app.desktop_id: index for index, app in enumerate(registered)}
        diagnostic_indices = {
            item.desktop_id: index for index, item in enumerate(diagnostics)
        }
        for app in self._registrations.applications():
            index = known.get(app.desktop_id)
            if index is not None:
                # A selected native shortcut can share the shell's identity
                # while carrying different arguments, artwork or working dir.
                # Keep association handles/actions from normal discovery.
                previous = registered[index]
                app = replace(
                    previous,
                    name=app.name,
                    executable_path=app.executable_path,
                    wm_class=app.wm_class or previous.wm_class,
                    aliases=tuple(dict.fromkeys((*app.aliases, *previous.aliases))),
                    working_directory=app.working_directory,
                    launch_target=app.launch_target,
                    document_target=app.document_target,
                    export_file=(
                        previous.export_file
                        if previous.launch_target == app.launch_target
                        else None
                    ),
                    export_data=(
                        previous.export_data
                        if previous.launch_target == app.launch_target
                        else ()
                    ),
                )
                registered[index] = app
            else:
                known[app.desktop_id] = len(registered)
                registered.append(app)
                order.append(app.desktop_id)
            diagnostic_index = diagnostic_indices.get(app.desktop_id)
            if diagnostic_index is not None:
                diagnostics[diagnostic_index] = replace(
                    diagnostics[diagnostic_index],
                    name=app.name,
                    wm_class=app.wm_class,
                    launcher_basename=app.wm_class,
                    aliases=app.aliases,
                )
            else:
                diagnostic_indices[app.desktop_id] = len(diagnostics)
                diagnostics.append(
                    ApplicationDiagnostic(
                        desktop_id=app.desktop_id,
                        name=app.name,
                        desktop_file=app.desktop_file,
                        visible=app.visible,
                        has_gio_source=app.has_gio_source,
                        wm_class=app.wm_class,
                        launcher_basename=app.wm_class,
                        aliases=app.aliases,
                    )
                )
        result = replace(
            result,
            applications=tuple(registered),
            diagnostics=tuple(diagnostics),
            presentation_order=tuple(order),
            directories=tuple(
                dict.fromkeys((*result.directories, *self._registrations.directories()))
            ),
        )

        hidden = self.lookup.hidden_ids
        result = replace(
            result,
            applications=tuple(
                replace(app, visible=False) if app.desktop_id in hidden else app
                for app in result.applications
            ),
            diagnostics=tuple(
                replace(item, visible=False) if item.desktop_id in hidden else item
                for item in result.diagnostics
            ),
            initial_discovery_pending=self.initial_discovery_pending,
        )
        self.lookup.request(app.desktop_id for app in result.applications)
        return result

    def steam(self) -> SteamDiscoveryResult:
        return SteamDiscoveryResult()

    def game_application(self, game: SteamGame) -> ApplicationInfo:
        # Native Steam discovery is not supplied by this port.
        raise NotImplementedError("Windows Steam manifest discovery is unavailable")

    def hidden_steam_id(self, path: Path) -> str | None:
        return None

    def create_monitor(self, changed: Callable[[], None]) -> CatalogMonitor:
        if self._changed is not None:
            raise RuntimeError("An application catalog belongs to one registry")
        self._changed = changed
        return WindowsCatalogMonitor(self)

    def _on_metadata_changed(self) -> None:
        if self._active:
            assert self._changed is not None
            self._changed()

    def _on_native_changed(self) -> None:
        if self._active:
            self.invalidate()
            self._on_metadata_changed()

    def metadata_resolver_factory(
        self,
        registrations: WindowsExecutableRegistration,
        *,
        read_default_source: bool,
    ) -> Callable[[], MetadataResolver]:
        def resolver() -> MetadataResolver:
            exporter = WindowsApplicationExport()

            def read(
                ids: tuple[str, ...],
                cancelled: Callable[[], bool],
                *,
                association_keys: tuple[AssociationKey, ...] = (),
            ) -> ApplicationMetadata:
                if cancelled():
                    return ApplicationMetadata()
                source = _read_gio_discovery() if read_default_source else None
                if cancelled():
                    return ApplicationMetadata()
                associations = read_associations(association_keys, cancelled)
                if cancelled():
                    return ApplicationMetadata()
                known_ids = set(ids)
                if source is not None:
                    known_ids.update(app.desktop_id for app in source.applications)
                association_ids = {
                    identifier
                    for _key, result in associations
                    for identifier in (
                        result.application_id,
                        *(
                            discovery.desktop_id(handle) or ""
                            for handle in result.handles
                        ),
                    )
                    if identifier
                }
                known_ids.update(association_ids)
                metadata = catalog.read_application_metadata(
                    tuple(sorted(known_ids)),
                    registrations=registrations.applications(),
                    cancelled=cancelled,
                    exporter=exporter,
                )
                if cancelled():
                    return ApplicationMetadata()
                return replace(
                    metadata,
                    source_discovery=source,
                    associations=associations,
                    association_ids=frozenset(association_ids),
                )

            def resolve(
                ids: tuple[str, ...],
                cancelled: Callable[[], bool],
                *,
                association_keys: tuple[AssociationKey, ...] = (),
            ) -> ApplicationMetadata:
                # Capture this monitor lifetime before checking cancellation;
                # a stopped worker must not report completion to its successor.
                monitor = self._change_monitor
                ticket = monitor.read_started() if monitor is not None else None
                try:
                    return read(ids, cancelled, association_keys=association_keys)
                finally:
                    if monitor is not None:
                        monitor.read_finished(ticket)

            return resolve

        return resolver

    def create_change_monitor(
        self,
        changed: Callable[[], None],
    ) -> CatalogChangeMonitor | None:
        self._change_monitor = None
        try:
            self._change_monitor = CatalogChangeMonitor(changed)
            return self._change_monitor
        except OSError as exc:
            log.warning(
                "Could not watch native application changes (error %s)", exc.errno
            )
            return None


class WindowsCatalogMonitor(CatalogMonitor):
    """Observe before reading, and stop every source from this lifetime."""

    def __init__(self, catalog: WindowsApplicationCatalogBackend) -> None:
        self._catalog = catalog
        self._generation = 0
        self._gio = GioCatalogMonitor(
            catalog._on_native_changed, catalog._registrations.directories
        )

    def start(self) -> None:
        catalog = self._catalog
        if catalog._active:
            return
        catalog._active = True
        self._generation += 1
        generation = self._generation

        def changed() -> None:
            if catalog._active and generation == self._generation:
                catalog._on_native_changed()

        self._gio = GioCatalogMonitor(changed, catalog._registrations.directories)
        catalog._change_monitor = catalog.create_change_monitor(changed)
        catalog.lookup.start()
        self._gio.start()

    def sync(self, directories: tuple[Path, ...]) -> None:
        self._gio.sync(directories)

    def stop(self) -> None:
        catalog = self._catalog
        if not catalog._active:
            return
        catalog._active = False
        self._generation += 1
        catalog.lookup.stop()
        self._gio.stop()
        monitor, catalog._change_monitor = catalog._change_monitor, None
        if monitor is not None:
            monitor.close()
