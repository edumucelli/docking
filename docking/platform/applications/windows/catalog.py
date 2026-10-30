"""Windows catalog policy and worker-owned native metadata construction."""

from collections.abc import Callable, Iterable

from gi.repository import Gio

from docking.log import get_logger
from docking.platform.backends.windows import catalog
from docking.platform.backends.windows.catalog_monitor import CatalogChangeMonitor

from ..catalog_backend import CatalogRequest
from ..discovery import DiscoveryResult
from ..metadata import ApplicationChangeMonitor, ApplicationMetadata, MetadataResolver
from ..registration import ExecutableRegistrationBackend
from .discovery import discover_windows_applications, merge_shell_catalog
from .export import WindowsApplicationExport

log = get_logger("application_metadata")


class WindowsApplicationCatalogBackend:
    def default_source(self) -> Iterable[object]:
        return Gio.AppInfo.get_all()

    def discover(self, request: CatalogRequest) -> DiscoveryResult:
        result = discover_windows_applications(request.application_source())
        return (
            merge_shell_catalog(result, request.catalog)
            if request.catalog is not None
            else result
        )

    def metadata_resolver_factory(
        self,
        registrations: ExecutableRegistrationBackend,
    ) -> Callable[[], MetadataResolver]:
        def resolver() -> MetadataResolver:
            exporter = WindowsApplicationExport()

            def resolve(
                ids: tuple[str, ...], cancelled: Callable[[], bool]
            ) -> ApplicationMetadata:
                return catalog.read_application_metadata(
                    ids,
                    registrations=registrations.applications(),
                    cancelled=cancelled,
                    exporter=exporter,
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
