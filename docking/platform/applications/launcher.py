"""Registry-selected application launches and shared process provenance."""

from collections.abc import Iterable

from .identity import LaunchProvenanceStore
from .launch_backend import (
    ApplicationLaunchBackend,
    PopenFactory,
    create_launch_backend,
)
from .launched_process import LaunchOutcome
from .projections import NEW_WINDOW_ACTION_ID, new_window_action, quicklist_actions
from .registry import ApplicationRegistry
from .types import ApplicationAction, ApplicationInfo, ApplicationLocation


class ApplicationLauncher:
    """Select registry records without publishing their native launch handles."""

    def __init__(
        self,
        registry: ApplicationRegistry,
        provenance_store: LaunchProvenanceStore,
        *,
        popen: PopenFactory | None = None,
        backend: ApplicationLaunchBackend | None = None,
    ) -> None:
        self._registry = registry
        self._provenance_store = provenance_store
        self._backend = (
            create_launch_backend(popen=popen) if backend is None else backend
        )

    def _record(self, application: ApplicationInfo, outcome: LaunchOutcome) -> bool:
        if outcome.started and outcome.process is not None:
            self._provenance_store.record_launch(
                process=outcome.process,
                desktop_id=application.desktop_id,
                executable_path=outcome.executable_path,
            )
        return outcome.started

    def quicklist_actions(self, desktop_id: str) -> list[ApplicationAction]:
        """Enumerate source-exclusive dock quicklist actions."""
        application = self._registry.get(desktop_id)
        return list(quicklist_actions(application)) if application is not None else []

    def launch(self, desktop_id: str) -> bool:
        """Activate one specifically selected application."""
        application = self._registry.get(desktop_id)
        if application is None:
            return False
        return self._record(
            application,
            self._backend.launch(
                application,
                lambda: self._registry._gio_handle_for(desktop_id),
            ),
        )

    def launch_action(self, desktop_id: str, action_id: str) -> bool:
        """Launch a source-aware action on the selected application."""
        application = self._registry.get(desktop_id)
        if application is None:
            return False
        action = next(
            (a for a in application.actions if a.action_id == action_id), None
        )
        return self._record(
            application,
            self._backend.launch_action(
                application,
                action_id,
                action,
                lambda: self._registry._gio_handle_for(desktop_id),
            ),
        )

    def launch_new_window(self, desktop_id: str) -> bool:
        """Open a new window when Gio declares that action, else launch normally."""
        application = self._registry.get(desktop_id)
        if application is None:
            return self.launch(desktop_id)

        action = new_window_action(application)
        if action is not None:
            if self.launch_action(desktop_id, NEW_WINDOW_ACTION_ID):
                return True
            if application.location is ApplicationLocation.HOST:
                # Once the host action exists, a missing Exec/flatpak-spawn
                # route does not launch the base application instead.
                return False
        return self.launch(desktop_id)

    def launch_app_uris(self, desktop_id: str, uris: Iterable[str]) -> bool:
        """Open a URI list with one specifically selected application."""
        launchable = list(uris)
        if not launchable:
            return False
        application = self._registry.get(desktop_id)
        if application is None:
            return False
        return self._record(
            application,
            self._backend.launch_uris(
                application,
                launchable,
                lambda: self._registry._gio_handle_for(desktop_id),
            ),
        )

    def launch_listing(self, listing_key: str) -> bool:
        """Launch a transient Gio-backed listing by its opaque registry token."""
        handle = self._registry._gio_handle_for_unidentified(listing_key)
        return (
            self._backend.launch_listing(listing_key, handle)
            if handle is not None
            else False
        )


__all__ = ["ApplicationLauncher"]
