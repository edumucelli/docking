"""Authoritative, immutable snapshots of installed application metadata."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.log import get_logger, with_context

from . import handle_adapters as discovery
from .associations import AssociationKey, AssociationResolution, AssociationStatus
from .backend import (
    ApplicationAssociationBackend,
    ApplicationCatalogBackend,
    ExecutableRegistrationBackend,
)
from .steam import SteamDiscoveryResult, steam_app_id_from_exec
from .types import (
    ApplicationDiagnostic,
    ApplicationDiscoveryDiagnostic,
    ApplicationInfo,
    DiscoveryReason,
    TransientApplicationInfo,
)

DEFAULT_DEBOUNCE_MS = 150
MAX_CONTENT_HANDLER_TOKENS = 64

RegistryListener = Callable[[], None]

log = with_context(get_logger(name="application_registry"))


@dataclass(frozen=True, slots=True)
class _RegistryState:
    generation: int
    directories: tuple[Path, ...]
    handle_epoch: int
    applications_by_id: Mapping[str, ApplicationInfo]
    visible: tuple[ApplicationInfo, ...]
    wm_class_index: Mapping[str, tuple[ApplicationInfo, ...]]
    desktop_file_index: Mapping[Path, ApplicationInfo]
    gio_handles: Mapping[str, object]
    unidentified: tuple[TransientApplicationInfo, ...]
    unidentified_gio_handles: Mapping[str, object]
    diagnostic: ApplicationDiscoveryDiagnostic
    steam_apps: Mapping[str, ApplicationInfo]
    steam_installs: tuple[tuple[Path, str], ...]


def _empty_state() -> _RegistryState:
    return _RegistryState(
        generation=0,
        directories=(),
        handle_epoch=0,
        applications_by_id=MappingProxyType({}),
        visible=(),
        wm_class_index=MappingProxyType({}),
        desktop_file_index=MappingProxyType({}),
        gio_handles=MappingProxyType({}),
        unidentified=(),
        unidentified_gio_handles=MappingProxyType({}),
        diagnostic=ApplicationDiscoveryDiagnostic(0, 0, 0, (), False),
        steam_apps=MappingProxyType({}),
        steam_installs=(),
    )


class ApplicationRegistry:
    """Discover applications and publish complete immutable generations.

    Lifecycle and Gio-backed operations belong to the GTK main-loop composition;
    immutable application snapshots may also be read by worker code.
    """

    def __init__(
        self,
        *,
        catalog_backend: ApplicationCatalogBackend,
        association_backend: ApplicationAssociationBackend,
        registration_backend: ExecutableRegistrationBackend,
    ) -> None:
        self._catalog = catalog_backend
        self._associations = association_backend
        self._registrations = registration_backend
        self._monitor = catalog_backend.create_monitor(self._on_registry_changed)
        self._schedule_timeout = GLib.timeout_add
        self._cancel_timeout = GLib.source_remove
        self._debounce_ms = DEFAULT_DEBOUNCE_MS

        self._state = _empty_state()
        self._loaded = False
        self._listeners: list[RegistryListener] = []
        self._started = False
        self._lifecycle_token = 0
        self._content_token_serial = 0
        self._content_gio_handles: dict[str, object] = {}

        self._debounce_source_id: int | None = None

    @property
    def generation(self) -> int:
        """Return the current immutable generation number."""
        return self._state.generation

    def diagnostic_snapshot(self) -> ApplicationDiscoveryDiagnostic:
        """Read discovery evidence without consulting Gio or scanning directories."""
        return self._state.diagnostic

    def snapshot(self) -> tuple[ApplicationInfo, ...]:
        """Return visible applications in stable presentation order."""
        return self._state.visible

    def unidentified_snapshot(
        self,
    ) -> tuple[TransientApplicationInfo, ...]:
        """Return visible Gio listings that cannot participate in ID lookup."""
        return self._state.unidentified

    def get(self, desktop_id: str) -> ApplicationInfo | None:
        """Read one canonical record without consulting Gio."""
        return self._state.applications_by_id.get(desktop_id)

    def resolve_all_by_wm_class(
        self,
        wm_class: str,
    ) -> tuple[ApplicationInfo, ...]:
        """Return every source-ordered record sharing an alias."""
        lookup = wm_class.lower().strip()
        if not lookup:
            return ()
        return self._state.wm_class_index.get(lookup, ())

    def resolve_by_wm_class(
        self,
        wm_class: str,
    ) -> ApplicationInfo | None:
        """Return the first source-ordered record sharing an alias."""
        matches = self.resolve_all_by_wm_class(wm_class)
        return matches[0] if matches else None

    def resolve_steam_app_id(self, app_id: str) -> ApplicationInfo | None:
        return self._state.steam_apps.get(app_id)

    def resolve_steam_executable(self, path: Path) -> ApplicationInfo | None:
        candidates = {
            app_id
            for directory, app_id in self._state.steam_installs
            if path.is_relative_to(directory)
        }
        if len(candidates) != 1:
            return None
        return self._state.steam_apps.get(next(iter(candidates)))

    def resolve_by_desktop_file(self, path: Path) -> ApplicationInfo | None:
        """Resolve an application by its exact or canonical desktop-file path."""
        for key in discovery.desktop_file_keys(path):
            application = self._state.desktop_file_index.get(key)
            if application is not None:
                return application
        return None

    def subscribe(self, callback: RegistryListener) -> Callable[[], None]:
        """Register a listener and return an idempotent unsubscribe."""
        if callback not in self._listeners:
            self._listeners.append(callback)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if not subscribed:
                return
            subscribed = False
            with suppress(ValueError):
                self._listeners.remove(callback)

        return unsubscribe

    def refresh(self) -> bool:
        """Build and atomically publish a complete generation."""
        current = self._state
        handle_epoch = current.handle_epoch + 1
        try:
            built = self._discover(handle_epoch=handle_epoch)
        except Exception as exc:
            log.bind(action="discover_applications").warning(
                "Failed to refresh application registry: %s",
                exc,
            )
            return False

        changed = not self._loaded or _state_content(current) != _state_content(built)
        self._loaded = True
        self._content_token_serial = 0
        if changed:
            self._state = replace(
                built,
                generation=current.generation + 1,
                diagnostic=replace(built.diagnostic, generation=current.generation + 1),
            )
            self._notify_listeners()
        else:
            self._state = replace(
                current,
                handle_epoch=handle_epoch,
                directories=built.directories,
                gio_handles=built.gio_handles,
                unidentified=built.unidentified,
                unidentified_gio_handles=built.unidentified_gio_handles,
                diagnostic=replace(built.diagnostic, generation=current.generation),
            )
        self._content_gio_handles.clear()

        if self._started:
            self._monitor.sync(self._state.diagnostic.steam_directories)
        return changed

    def start(self) -> None:
        """Start monitors and synchronously populate the registry."""
        if self._started:
            return
        self._started = True
        self._lifecycle_token += 1
        self._monitor.start()
        self._monitor.sync(self._state.diagnostic.steam_directories)
        self.refresh()

    def stop(self) -> None:
        """Stop monitors while retaining the last immutable generation."""
        if not self._started:
            return
        self._started = False
        self._lifecycle_token += 1
        self._cancel_pending_refresh()
        self._monitor.stop()

    def default_for_content_type(
        self,
        content_type: str,
    ) -> AssociationResolution:
        """Resolve the desktop's default handler on the calling main thread."""
        read = self._associations.read(AssociationKey("default", content_type))
        handle = read.handles[0] if read.handles else None
        return AssociationResolution(
            read.status, listing=self._application_for_content_type_result(handle)
        )

    def _default_listing_for_content_type(
        self,
        content_type: str,
    ) -> ApplicationInfo | TransientApplicationInfo | None:
        """Return a launchable default handler with any Gio handle retained."""
        read = self._associations.read(AssociationKey("default", content_type))
        if read.status is not AssociationStatus.READY:
            self._association_status = read.status
        return self._listing_for_content_type_result(
            read.handles[0] if read.handles else None
        )

    def _recommended_listings_for_content_type(
        self,
        content_type: str,
    ) -> tuple[ApplicationInfo | TransientApplicationInfo, ...]:
        """Return visible recommended handlers with private Gio handles retained."""
        return self._listings_for_content_type(
            content_type=content_type,
            lookup_name="get_recommended_for_type",
            action="recommended_listings_for_content_type",
        )

    def _all_listings_for_content_type(
        self,
        content_type: str,
    ) -> tuple[ApplicationInfo | TransientApplicationInfo, ...]:
        """Return every visible handler with private Gio handles retained."""
        return self._listings_for_content_type(
            content_type=content_type,
            lookup_name="get_all_for_type",
            action="all_listings_for_content_type",
        )

    def preferred_listing_for_content_types(
        self,
        content_types: Iterable[str],
    ) -> AssociationResolution:
        """Select a visible handler using desktop association precedence.

        Defaults are considered for every content type first, followed by
        recommended handlers for every type, and finally the complete handler
        lists. This preserves the established media-launcher fallback order while
        keeping Gio association policy inside the registry.
        """
        self._association_status = AssociationStatus.READY
        ordered_types = tuple(content_types)
        for content_type in ordered_types:
            listing = self._default_listing_for_content_type(content_type)
            if listing is not None:
                return AssociationResolution(AssociationStatus.READY, listing=listing)
        for lookup in (
            self._recommended_listings_for_content_type,
            self._all_listings_for_content_type,
        ):
            for content_type in ordered_types:
                listings = lookup(content_type)
                if listings:
                    return AssociationResolution(
                        AssociationStatus.READY, listing=listings[0]
                    )
        return AssociationResolution(self._association_status)

    def _listings_for_content_type(
        self,
        *,
        content_type: str,
        lookup_name: str,
        action: str,
    ) -> tuple[ApplicationInfo | TransientApplicationInfo, ...]:
        """Convert one Gio association result into canonical visible listings."""
        key = AssociationKey(
            "recommended" if lookup_name == "get_recommended_for_type" else "all",
            content_type,
        )
        read = self._associations.read(key)
        if read.status is not AssociationStatus.READY:
            self._association_status = read.status
        app_infos = read.handles

        result: list[ApplicationInfo | TransientApplicationInfo] = []
        seen_registered: set[str] = set()
        seen_unregistered: set[tuple[str, object]] = set()
        retained_count = 0
        retained_limit = max(1, MAX_CONTENT_HANDLER_TOKENS)
        for app_info in app_infos or ():
            if not self._associations.visible(app_info):
                continue
            registered = self._registered_application_for_content_type_result(app_info)
            if registered is not None:
                if not registered.visible or registered.desktop_id in seen_registered:
                    continue
                seen_registered.add(registered.desktop_id)
                result.append(registered)
                continue

            desktop_id = discovery.desktop_id(app_info)
            identity: tuple[str, object] = (
                ("desktop-id", desktop_id) if desktop_id else ("object", id(app_info))
            )
            if identity in seen_unregistered:
                continue
            seen_unregistered.add(identity)
            if retained_count >= retained_limit:
                continue
            result.append(self._retain_content_listing(app_info))
            retained_count += 1
        return tuple(result)

    def directory_application_name(self) -> AssociationResolution:
        read = self._associations.read(AssociationKey("name", "inode/directory"))
        return AssociationResolution(read.status, name=read.name)

    @property
    def registrations(self) -> ExecutableRegistrationBackend:
        return self._registrations

    def _gio_handle_for(self, desktop_id: str) -> Any | None:
        """Return a generation-owned Gio handle for future main-thread services."""
        return self._state.gio_handles.get(desktop_id)

    def _gio_handle_for_unidentified(self, listing_key: str) -> Any | None:
        """Return the private Gio handle for an opaque launchable listing."""
        handle = self._state.unidentified_gio_handles.get(listing_key)
        if handle is not None:
            return handle
        return self._content_gio_handles.get(listing_key)

    def _discover(self, *, handle_epoch: int) -> _RegistryState:
        result = self._catalog.discover(handle_epoch=handle_epoch)
        try:
            steam = self._catalog.steam()
        except Exception as exc:
            log.bind(action="discover_steam_games").warning(
                "Failed to discover Steam games: %s", exc
            )
            steam = SteamDiscoveryResult()
        applications = list(result.applications)
        diagnostics = list(result.diagnostics)
        steam_apps: dict[str, ApplicationInfo] = {}
        for index, app in enumerate(applications):
            app_id = steam_app_id_from_exec(app.exec_line)
            if app_id:
                app = replace(app, steam_app_id=app_id)
                applications[index] = app
                steam_apps.setdefault(app_id, app)
        steam_ids = {app.desktop_id: app.steam_app_id for app in applications}
        diagnostics = [
            replace(item, steam_app_id=steam_ids.get(item.desktop_id, ""))
            for item in diagnostics
        ]
        hidden: set[str] = set()
        hidden_ids: set[str] = set()
        for decision in result.decisions:
            if decision.reason is DiscoveryReason.HIDDEN:
                hidden_ids.add(decision.desktop_id)
                if decision.desktop_file is not None:
                    app_id = self._catalog.hidden_steam_id(decision.desktop_file)
                    if app_id:
                        hidden.add(app_id)
        known_ids = {app.desktop_id for app in applications}
        for game in steam.games:
            if game.app_id in steam_apps or game.app_id in hidden:
                continue
            app = self._catalog.game_application(game)
            if app.desktop_id in hidden_ids or app.desktop_id in known_ids:
                continue
            steam_apps[game.app_id] = app
            known_ids.add(app.desktop_id)
            applications.append(app)
            diagnostics.append(
                ApplicationDiagnostic(
                    desktop_id=app.desktop_id,
                    name=app.name,
                    desktop_file=None,
                    visible=False,
                    has_gio_source=False,
                    wm_class=app.wm_class,
                    launcher_basename="flatpak" if game.flatpak else "steam",
                    aliases=app.aliases,
                    steam_app_id=game.app_id,
                    steam_manifest=game.manifest,
                )
            )
        state = _build_state(
            handle_epoch=handle_epoch,
            applications=applications,
            handles=result.handles,
            unidentified=result.transient,
            unidentified_handles=result.transient_handles,
            presentation_order=result.presentation_order,
            directories=result.directories,
        )
        return replace(
            state,
            steam_apps=MappingProxyType(steam_apps),
            steam_installs=tuple(
                (game.install_dir, game.app_id) for game in steam.games
            ),
            diagnostic=replace(
                state.diagnostic,
                applications=tuple(diagnostics),
                decisions=result.decisions,
                directory_statuses=result.directory_statuses,
                steam_directories=steam.watch_directories,
                steam_issues=steam.issues,
            ),
        )

    def _application_for_content_type_result(
        self,
        app_info: object | None,
    ) -> ApplicationInfo | None:
        return self._associations.application(self, app_info)

    def _registered_application_for_content_type_result(
        self,
        app_info: object,
    ) -> ApplicationInfo | None:
        return self._associations.registered(self, app_info)

    def _listing_for_content_type_result(
        self,
        app_info: object | None,
    ) -> ApplicationInfo | TransientApplicationInfo | None:
        return self._associations.listing(self, app_info)

    def _retain_content_listing(self, app_info: object) -> TransientApplicationInfo:
        self._content_token_serial += 1
        listing_key = (
            f"gio-content:{self._state.handle_epoch}:{self._content_token_serial}"
        )
        listing = self._associations.transient(app_info, listing_key)
        self._content_gio_handles[listing_key] = app_info
        while len(self._content_gio_handles) > max(1, MAX_CONTENT_HANDLER_TOKENS):
            self._content_gio_handles.pop(next(iter(self._content_gio_handles)))
        return listing

    def _on_registry_changed(self, *_args: object) -> None:
        self._queue_refresh()

    def _queue_refresh(self) -> None:
        if not self._started or self._debounce_source_id is not None:
            return
        lifecycle_token = self._lifecycle_token
        source_id: int | None = None

        def run_refresh() -> bool:
            if self._debounce_source_id == source_id:
                self._debounce_source_id = None
            if not self._started or lifecycle_token != self._lifecycle_token:
                return False
            self.refresh()
            return False

        source_id = self._schedule_timeout(
            self._debounce_ms,
            run_refresh,
        )
        self._debounce_source_id = source_id

    def _cancel_pending_refresh(self) -> None:
        source_id = self._debounce_source_id
        self._debounce_source_id = None
        if source_id is None:
            return
        with suppress(TypeError, ValueError, GLib.Error):
            self._cancel_timeout(source_id)

    def _notify_listeners(self) -> None:
        for callback in tuple(self._listeners):
            try:
                callback()
            except Exception as exc:
                log.bind(action="notify_registry_listener").warning(
                    "Application registry listener failed: %s",
                    exc,
                )


def _state_content(state: _RegistryState) -> tuple[object, ...]:
    return (
        state.applications_by_id,
        state.visible,
        state.wm_class_index,
        state.desktop_file_index,
        state.unidentified,
        state.steam_apps,
        state.steam_installs,
    )


def _build_state(
    *,
    handle_epoch: int,
    applications: Iterable[ApplicationInfo],
    handles: Mapping[str, object],
    unidentified: Iterable[TransientApplicationInfo],
    unidentified_handles: Mapping[str, object],
    presentation_order: Iterable[str],
    directories: tuple[Path, ...] = (),
) -> _RegistryState:
    applications_by_id: dict[str, ApplicationInfo] = {}
    for application in applications:
        if application.desktop_id in applications_by_id:
            continue
        applications_by_id[application.desktop_id] = application
    ordered_applications = tuple(applications_by_id.values())

    visible_ids: set[str] = set()
    visible_list: list[ApplicationInfo] = []
    for desktop_id in presentation_order:
        application = applications_by_id.get(desktop_id)
        if application is None or not application.visible or desktop_id in visible_ids:
            continue
        visible_ids.add(desktop_id)
        visible_list.append(application)
    visible_list.extend(
        application
        for application in ordered_applications
        if application.visible and application.desktop_id not in visible_ids
    )
    visible = tuple(visible_list)

    alias_lists: dict[str, list[ApplicationInfo]] = {}
    desktop_file_index: dict[Path, ApplicationInfo] = {}
    for application in ordered_applications:
        for alias in application.aliases:
            candidates = alias_lists.setdefault(alias, [])
            if all(
                candidate.desktop_id != application.desktop_id
                for candidate in candidates
            ):
                candidates.append(application)
        if application.desktop_file is not None:
            for key in discovery.desktop_file_keys(application.desktop_file):
                desktop_file_index.setdefault(key, application)

    unidentified_snapshot = tuple(unidentified)
    listing_keys = {application.listing_key for application in unidentified_snapshot}

    return _RegistryState(
        generation=0,
        steam_apps=MappingProxyType({}),
        steam_installs=(),
        directories=directories,
        handle_epoch=handle_epoch,
        applications_by_id=MappingProxyType(applications_by_id),
        visible=visible,
        wm_class_index=_freeze_plural_index(alias_lists),
        desktop_file_index=MappingProxyType(desktop_file_index),
        gio_handles=MappingProxyType(
            {
                desktop_id: handle
                for desktop_id, handle in handles.items()
                if desktop_id in applications_by_id
            }
        ),
        unidentified=unidentified_snapshot,
        unidentified_gio_handles=MappingProxyType(
            {
                listing_key: handle
                for listing_key, handle in unidentified_handles.items()
                if listing_key in listing_keys
            }
        ),
        diagnostic=ApplicationDiscoveryDiagnostic(
            generation=0,
            registered_count=len(applications_by_id),
            visible_count=len(visible),
            directories=directories,
            loaded=True,
        ),
    )


def _freeze_plural_index(
    values: Mapping[Any, Iterable[ApplicationInfo]],
) -> Mapping[Any, tuple[ApplicationInfo, ...]]:
    return MappingProxyType(
        {key: tuple(candidates) for key, candidates in values.items()}
    )


__all__ = [
    "DEFAULT_DEBOUNCE_MS",
    "MAX_CONTENT_HANDLER_TOKENS",
    "ApplicationRegistry",
    "RegistryListener",
]
