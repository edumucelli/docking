"""Registry selection, action policy and shared launch provenance."""

from __future__ import annotations

from collections.abc import Iterable

from .backend import ApplicationLaunchBackend, LaunchResult
from .identity import LaunchProvenanceStore
from .projections import NEW_WINDOW_ACTION_ID, new_window_action, quicklist_actions
from .registry import ApplicationRegistry
from .types import ApplicationAction, ApplicationInfo, ApplicationLocation


class ApplicationLauncher:
    def __init__(
        self,
        registry: ApplicationRegistry,
        provenance_store: LaunchProvenanceStore,
        *,
        backend: ApplicationLaunchBackend,
    ) -> None:
        self._registry = registry
        self._provenance_store = provenance_store
        self._backend = backend

    def _record(self, application: ApplicationInfo, result: LaunchResult) -> bool:
        if result.accepted and result.launched is not None:
            self._provenance_store.record_launch(
                process=result.launched.process,
                desktop_id=application.desktop_id,
                executable_path=result.launched.executable_path,
            )
        return result.accepted

    def launch(self, desktop_id: str) -> bool:
        application = self._registry.get(desktop_id)
        return (
            self._record(application, self._backend.launch(application))
            if application is not None
            else False
        )

    def launch_action(self, desktop_id: str, action_id: str) -> bool:
        application = self._registry.get(desktop_id)
        if application is None:
            return False
        return self._record(
            application,
            self._backend.launch_action(
                application, action_id, self._registry._gio_handle_for(desktop_id)
            ),
        )

    def launch_app_uris(self, desktop_id: str, uris: Iterable[str]) -> bool:
        application = self._registry.get(desktop_id)
        if application is None:
            return False
        return self._record(
            application,
            self._backend.launch_app_uris(
                application, uris, self._registry._gio_handle_for(desktop_id)
            ),
        )

    def launch_listing(self, listing_key: str) -> bool:
        return self._backend.launch_listing(
            listing_key, self._registry._gio_handle_for_unidentified(listing_key)
        ).accepted

    def quicklist_actions(
        self,
        desktop_id: str,
    ) -> list[ApplicationAction]:
        """Enumerate source-exclusive dock quicklist actions."""
        application = self._registry.get(desktop_id)
        if application is None:
            return []
        return list(quicklist_actions(application))

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


__all__ = ["ApplicationLauncher"]
