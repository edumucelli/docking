"""Unix catalogs use Gio and desktop-file sources through shared discovery."""

from collections.abc import Callable, Iterable

from . import discovery
from .catalog_backend import CatalogRequest
from .discovery import DiscoveryResult
from .metadata import ApplicationChangeMonitor, MetadataResolver
from .registration import ExecutableRegistrationBackend


class UnixApplicationCatalogBackend:
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
    ) -> Callable[[], MetadataResolver] | None:
        return None

    def create_change_monitor(
        self,
        _changed: Callable[[], None],
    ) -> ApplicationChangeMonitor | None:
        return None
