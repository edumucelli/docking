"""Unix catalogs use Gio and desktop-file sources through shared discovery."""

from collections.abc import Callable, Iterable

import gi

from . import discovery
from .associations import AssociationKey, AssociationRead, AssociationStatus
from .catalog_backend import CatalogRequest
from .discovery import DiscoveryResult
from .metadata import (
    ApplicationChangeMonitor,
    ApplicationMetadataLookup,
    MetadataResolver,
)
from .registration import ExecutableRegistrationBackend

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib


class UnixApplicationCatalogBackend:
    def association_reads(
        self, keys: tuple[AssociationKey, ...], lookup: ApplicationMetadataLookup | None
    ) -> Iterable[tuple[AssociationKey, AssociationRead]]:
        for key in keys:
            try:
                if key.lookup in ("default", "name"):
                    handle = Gio.AppInfo.get_default_for_type(key.content_type, False)
                    result = AssociationRead(
                        AssociationStatus.READY,
                        (handle,) if handle is not None else (),
                        name=handle.get_display_name() or None
                        if key.lookup == "name" and handle is not None
                        else None,
                    )
                else:
                    # Gio versions/platforms expose these named association APIs
                    # through an external compatibility boundary.
                    method = getattr(
                        Gio.AppInfo,
                        "get_recommended_for_type"
                        if key.lookup == "recommended"
                        else "get_all_for_type",
                        None,
                    )
                    result = (
                        AssociationRead(
                            AssociationStatus.READY,
                            tuple(method(key.content_type) or ()),
                        )
                        if callable(method)
                        else AssociationRead(AssociationStatus.UNAVAILABLE)
                    )
            except (GLib.Error, OSError):
                result = AssociationRead(AssociationStatus.UNAVAILABLE)
            yield key, result

    def default_source(self) -> Iterable[object]:
        return discovery.default_gio_applications()

    def discover(self, request: CatalogRequest) -> DiscoveryResult:
        return discovery.discover(
            application_source=request.application_source,
            desktop_directories_source=request.desktop_directories_source,
            desktop_app_info_for_id=request.desktop_app_info_for_id,
            desktop_app_info_from_filename=request.desktop_app_info_from_filename,
            handle_epoch=request.handle_epoch,
        )

    def metadata_resolver_factory(
        self,
        _registrations: ExecutableRegistrationBackend,
        *,
        read_default_source: bool,
    ) -> Callable[[], MetadataResolver] | None:
        return None

    def create_change_monitor(
        self,
        _changed: Callable[[], None],
    ) -> ApplicationChangeMonitor | None:
        return None
