"""Windows catalog policy and worker-owned native metadata construction."""

from collections.abc import Callable, Iterable
from dataclasses import replace

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger
from docking.platform.backends.windows import catalog
from docking.platform.backends.windows.catalog_monitor import CatalogChangeMonitor

from .. import discovery
from ..associations import AssociationKey, AssociationReads
from ..catalog_backend import CatalogRequest
from ..discovery import DiscoveryResult
from ..metadata import (
    ApplicationChangeMonitor,
    ApplicationMetadata,
    ApplicationMetadataLookup,
    MetadataResolver,
)
from ..registration import ExecutableRegistrationBackend
from .associations import read_associations
from .discovery import discover_windows_applications, merge_shell_catalog
from .export import WindowsApplicationExport

log = get_logger("application_metadata")


def _read_gio_discovery() -> DiscoveryResult | None:
    """Resolve Win32 AppInfo's potentially rebuilding cache on the worker."""
    try:
        return discover_windows_applications(tuple(Gio.AppInfo.get_all()))
    except (GLib.Error, OSError) as exc:
        log.debug("Could not read Windows Gio application metadata: %s", exc)
        return None


class WindowsApplicationCatalogBackend:
    def association_reads(
        self, keys: tuple[AssociationKey, ...], lookup: ApplicationMetadataLookup | None
    ) -> AssociationReads:
        assert lookup is not None
        return lookup.association_reads(keys)

    def default_source(self) -> Iterable[object]:
        # The first complete source arrives through the metadata worker.
        return ()

    def discover(self, request: CatalogRequest) -> DiscoveryResult:
        result = (
            request.source_discovery
            if request.source_discovery is not None
            else discover_windows_applications(request.application_source())
        )
        return (
            merge_shell_catalog(result, request.catalog)
            if request.catalog is not None
            else result
        )

    def metadata_resolver_factory(
        self,
        registrations: ExecutableRegistrationBackend,
        *,
        read_default_source: bool,
    ) -> Callable[[], MetadataResolver]:
        def resolver() -> MetadataResolver:
            exporter = WindowsApplicationExport()

            def resolve(
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

            return resolve

        return resolver

    def create_change_monitor(
        self,
        changed: Callable[[], None],
    ) -> ApplicationChangeMonitor | None:
        try:
            return CatalogChangeMonitor(changed)
        except OSError as exc:
            log.warning(
                "Could not watch native application changes (error %s)", exc.errno
            )
            return None
