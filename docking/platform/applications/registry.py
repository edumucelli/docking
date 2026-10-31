"""Authoritative, immutable snapshots of installed application metadata."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger, with_context

from . import discovery
from . import entries as desktop_entries
from .associations import (
    AssociationKey,
    AssociationRead,
    AssociationResolution,
    AssociationStatus,
)
from .catalog_backend import (
    ApplicationCatalogBackend,
    CatalogRequest,
    create_catalog_backend,
)
from .metadata import (
    ApplicationChangeMonitor,
    create_metadata_lookup,
)
from .registration import ExecutableRegistrationBackend, create_registration_backend
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


def _content_handler_is_visible(app_info: object) -> bool:
    return (
        discovery.safe_call(app_info, "should_show") is not False
        and not bool(discovery.safe_call(app_info, "get_is_hidden"))
        and not bool(discovery.safe_call(app_info, "get_nodisplay"))
    )


@dataclass(frozen=True, slots=True)
class _RegistryState:
    generation: int
    initial_discovery_pending: bool
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
        initial_discovery_pending=False,
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
        application_source: Callable[[], Iterable[object]] | None = None,
        desktop_directories_source: Callable[[], Iterable[Path]] | None = None,
        steam_source: Callable[[], SteamDiscoveryResult] | None = None,
        executable_registration: ExecutableRegistrationBackend | None = None,
        catalog_backend: ApplicationCatalogBackend | None = None,
    ) -> None:
        self._catalog_backend = (
            create_catalog_backend() if catalog_backend is None else catalog_backend
        )
        self._uses_default_source = application_source is None
        self._application_source = (
            self._catalog_backend.default_source
            if application_source is None
            else application_source
        )
        self._steam_source = steam_source
        self._executable_registration = (
            create_registration_backend()
            if executable_registration is None
            else executable_registration
        )
        self._desktop_directories_source = (
            desktop_directories_source or desktop_entries.desktop_dirs
        )
        desktop_app_info = getattr(Gio, "DesktopAppInfo", None)
        self._desktop_app_info_for_id = getattr(
            desktop_app_info, "new", lambda _id: None
        )
        self._desktop_app_info_from_filename = getattr(
            desktop_app_info, "new_from_filename", lambda _path: None
        )
        self._app_monitor_factory = Gio.AppInfoMonitor.get
        self._directory_monitor_factory = _monitor_directory
        self._schedule_timeout = GLib.timeout_add
        self._cancel_timeout = GLib.source_remove
        self._debounce_ms = DEFAULT_DEBOUNCE_MS

        self._state = _empty_state()
        self._loaded = False
        self._association_revision = 0
        self._listeners: list[RegistryListener] = []
        self._started = False
        self._lifecycle_token = 0
        self._content_token_serial = 0
        self._content_gio_handles: dict[str, object] = {}

        self._app_monitor: object | None = None
        self._change_monitor: ApplicationChangeMonitor | None = None
        self._change_monitor_factory = self._catalog_backend.create_change_monitor
        self._app_monitor_handler: object | None = None
        self._directory_monitors: dict[Path, tuple[object, object | None]] = {}
        self._debounce_source_id: int | None = None
        self._metadata = create_metadata_lookup(
            on_changed=self._queue_refresh,
            schedule_idle=GLib.idle_add,
            remove_source=GLib.source_remove,
            resolve_factory=self._catalog_backend.metadata_resolver_factory(
                self._executable_registration,
                read_default_source=self._uses_default_source,
            ),
        )
        self._state = replace(
            self._state,
            initial_discovery_pending=(
                self._metadata is not None
                and (
                    self._metadata.applications is None
                    or (
                        self._uses_default_source
                        and self._metadata.source_discovery is None
                    )
                )
            ),
        )

    @property
    def generation(self) -> int:
        """Return the current immutable generation number."""
        return self._state.generation

    @property
    def initial_discovery_pending(self) -> bool:
        """Whether an optional catalog still lacks its first complete result."""
        return self._state.initial_discovery_pending

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

    def resolve_by_export_file(self, path: Path) -> ApplicationInfo | None:
        """Read a prepared launcher's canonical identity without filesystem IO."""
        return next(
            (
                app
                for app in self._state.applications_by_id.values()
                if app.export_file == path
            ),
            None,
        )

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
        revision = (
            self._metadata.association_revision if self._metadata is not None else 0
        )
        association_changed = revision != self._association_revision
        self._association_revision = revision
        if changed or association_changed:
            self._notify_listeners()

        if self._started:
            self._sync_directory_monitors()
            if self._metadata is not None:
                self._metadata.request(self._state.applications_by_id)
        return changed

    def register_executable(
        self, target: str | Path, *, startup_wm_class: str = ""
    ) -> ApplicationInfo | None:
        """Resolve a persisted launcher, or reject a non-executable with None.

        An unresolvable saved launcher raises OSError so callers cannot
        reinterpret a catalog failure as an ordinary file pin.
        """
        identifier = self._executable_registration.create(
            target, startup_wm_class=startup_wm_class
        )
        if identifier is None:
            return None
        if self._metadata is not None:
            self._metadata.invalidate()
        self.refresh()
        application = self.get(identifier)
        if application is None:
            raise OSError("Persisted executable launcher could not be resolved")
        return application

    def start(self) -> None:
        """Start monitors and request the initial platform discovery."""
        if self._started:
            return
        self._started = True
        self._lifecycle_token += 1
        if self._metadata is not None:
            self._metadata.start()
        lifecycle = self._lifecycle_token

        def native_changed() -> None:
            if self._started and lifecycle == self._lifecycle_token:
                self._on_registry_changed()

        self._change_monitor = self._change_monitor_factory(native_changed)
        self._connect_app_monitor()
        self._sync_directory_monitors()
        self.refresh()

    def stop(self) -> None:
        """Stop monitors while retaining the last immutable generation."""
        if not self._started:
            return
        self._started = False
        self._lifecycle_token += 1
        self._cancel_pending_refresh()
        self._disconnect_app_monitor()
        if self._change_monitor is not None:
            self._change_monitor.close()
            self._change_monitor = None
        self._cancel_directory_monitors()
        if self._metadata is not None:
            self._metadata.stop()

    def default_for_content_type(self, content_type: str) -> AssociationResolution:
        key = AssociationKey("default", content_type)
        _key, read = next(
            iter(self._catalog_backend.association_reads((key,), self._metadata))
        )
        if read.status is not AssociationStatus.READY or read.application_id:
            return self._association_resolution(read)
        handle = read.handles[0] if read.handles else None
        if handle is None:
            return AssociationResolution(AssociationStatus.READY)
        identifier = discovery.desktop_id(handle)
        if self._metadata is not None and identifier in self._metadata.hidden_ids:
            return AssociationResolution(AssociationStatus.READY)
        application = self._registered_application_for_content_type_result(handle)
        # Keep canonical default-handler behavior, including Unix NoDisplay
        # handlers. An unknown handler needs a retained launch token instead.
        listing = (
            application
            if application is not None
            else self._listing_for_content_type_result(handle)
        )
        return AssociationResolution(AssociationStatus.READY, listing)

    def directory_application_name(self) -> AssociationResolution:
        key = AssociationKey("name", "inode/directory")
        _key, result = next(
            iter(self._catalog_backend.association_reads((key,), self._metadata))
        )
        return AssociationResolution(result.status, name=result.name)

    def preferred_listing_for_content_types(
        self, content_types: Iterable[str]
    ) -> AssociationResolution:
        """Preserve default/recommended/all precedence over accepted facts."""
        types = tuple(dict.fromkeys(content_types))
        keys = tuple(
            AssociationKey(kind, content_type)
            for kind in ("default", "recommended", "all")
            for content_type in types
        )
        unavailable = False
        for _key, read in self._catalog_backend.association_reads(keys, self._metadata):
            if read.status is AssociationStatus.PENDING:
                return AssociationResolution(AssociationStatus.PENDING)
            if read.status is AssociationStatus.UNAVAILABLE:
                unavailable = True
                continue
            result = self._association_resolution(read)
            if result.listing is not None:
                return result
            unavailable |= result.status is AssociationStatus.UNAVAILABLE
        return AssociationResolution(
            AssociationStatus.UNAVAILABLE if unavailable else AssociationStatus.READY
        )

    def _association_resolution(self, read: AssociationRead) -> AssociationResolution:
        if read.status is not AssociationStatus.READY:
            return AssociationResolution(read.status)
        if read.application_id:
            application = self.get(read.application_id)
            if application is None or not application.visible:
                return AssociationResolution(AssociationStatus.UNAVAILABLE)
            return AssociationResolution(AssociationStatus.READY, application)
        listings = self._association_listings(read.handles)
        return AssociationResolution(
            AssociationStatus.READY, listings[0] if listings else None
        )

    def _association_listings(
        self, app_infos: Iterable[object]
    ) -> tuple[ApplicationInfo | TransientApplicationInfo, ...]:
        """Materialize accepted opaque handles only on the registry's GTK thread."""
        result: list[ApplicationInfo | TransientApplicationInfo] = []
        seen_registered: set[str] = set()
        seen_unregistered: set[tuple[str, object]] = set()
        retained_count = 0
        retained_limit = max(1, MAX_CONTENT_HANDLER_TOKENS)
        for app_info in app_infos:
            if not _content_handler_is_visible(app_info):
                continue
            desktop_id = discovery.desktop_id(app_info)
            if self._metadata is not None and desktop_id in self._metadata.hidden_ids:
                continue
            registered = self._registered_application_for_content_type_result(app_info)
            if registered is not None:
                if registered.desktop_id in seen_registered:
                    continue
                if registered.visible:
                    seen_registered.add(registered.desktop_id)
                    result.append(registered)
                    continue
                if not self._is_association_only_handler(registered):
                    continue

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
        result = self._catalog_backend.discover(
            CatalogRequest(
                application_source=self._application_source,
                desktop_directories_source=self._desktop_directories_source,
                desktop_app_info_for_id=self._desktop_app_info_for_id,
                desktop_app_info_from_filename=self._desktop_app_info_from_filename,
                handle_epoch=handle_epoch,
                catalog=self._metadata.applications
                if self._metadata is not None
                else None,
                source_discovery=self._metadata.source_discovery
                if self._metadata is not None and self._uses_default_source
                else None,
            )
        )
        registered = list(result.applications)
        diagnostics = list(result.diagnostics)
        order = list(result.presentation_order)
        known = {app.desktop_id: index for index, app in enumerate(registered)}
        diagnostic_indices = {
            item.desktop_id: index for index, item in enumerate(diagnostics)
        }
        for app in self._executable_registration.applications():
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
                dict.fromkeys(
                    (*result.directories, *self._executable_registration.directories())
                )
            ),
        )
        try:
            steam = (
                self._steam_source()
                if self._steam_source is not None
                else SteamDiscoveryResult()
            )
        except Exception as exc:
            log.bind(action="discover_steam_games").warning(
                "Failed to discover Steam games: %s", exc
            )
            steam = SteamDiscoveryResult()
        visibility_hidden = (
            self._metadata.hidden_ids if self._metadata is not None else frozenset()
        )
        applications = [
            replace(app, visible=False) if app.desktop_id in visibility_hidden else app
            for app in result.applications
        ]
        diagnostics = [
            replace(item, visible=False)
            if item.desktop_id in visibility_hidden
            else item
            for item in result.diagnostics
        ]
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
                    facts = discovery.file_facts(decision.desktop_file)
                    app_id = steam_app_id_from_exec(facts.exec_line) if facts else None
                    if app_id:
                        hidden.add(app_id)
        known_ids = {app.desktop_id for app in applications}
        for game in steam.games:
            if game.app_id in steam_apps or game.app_id in hidden:
                continue
            app = game.application()
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
            initial_discovery_pending=(
                self._metadata is not None
                and (
                    self._metadata.applications is None
                    or (
                        self._uses_default_source
                        and self._metadata.source_discovery is None
                    )
                )
            ),
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

    def _registered_application_for_content_type_result(
        self,
        app_info: object,
    ) -> ApplicationInfo | None:
        desktop_id = discovery.desktop_id(app_info)
        if desktop_id:
            application = self._state.applications_by_id.get(desktop_id)
            if application is not None:
                return application

        filename = discovery.source_text(discovery.safe_call(app_info, "get_filename"))
        if not filename:
            return None
        path = Path(filename).expanduser()
        for key in discovery.desktop_file_keys(path):
            application = self._state.desktop_file_index.get(key)
            if application is not None:
                return application
        return None

    def _listing_for_content_type_result(
        self,
        app_info: object | None,
    ) -> ApplicationInfo | TransientApplicationInfo | None:
        if app_info is None or not _content_handler_is_visible(app_info):
            return None
        registered = self._registered_application_for_content_type_result(app_info)
        if registered is not None:
            if registered.visible:
                return registered
            if self._is_association_only_handler(registered):
                # Native menu membership is narrower than file associations.
                # Keep the original Gio handler for an association-only client.
                return self._retain_content_listing(app_info)
            return None
        return self._retain_content_listing(app_info)

    def _is_association_only_handler(self, application: ApplicationInfo) -> bool:
        return (
            self._metadata is not None
            and self._metadata.applications is not None
            and application.desktop_id not in self._metadata.hidden_ids
        )

    def _retain_content_listing(
        self,
        app_info: object,
    ) -> TransientApplicationInfo:
        self._content_token_serial += 1
        state = self._state
        listing_key = f"gio-content:{state.handle_epoch}:{self._content_token_serial}"
        listing = discovery.transient_from_gio(
            app_info=app_info,
            listing_key=listing_key,
            require_visible=False,
        )
        if listing is None:
            raise AssertionError("unfiltered Gio listing unexpectedly missing")

        self._content_gio_handles[listing_key] = app_info
        while len(self._content_gio_handles) > max(1, MAX_CONTENT_HANDLER_TOKENS):
            self._content_gio_handles.pop(next(iter(self._content_gio_handles)))
        return listing

    def _connect_app_monitor(self) -> None:
        try:
            monitor = self._app_monitor_factory()
            handler = monitor.connect("changed", self._on_registry_changed)
        except Exception as exc:
            log.bind(action="monitor_app_info").warning(
                "Could not monitor Gio application changes: %s",
                exc,
            )
            return
        self._app_monitor = monitor
        self._app_monitor_handler = handler

    def _disconnect_app_monitor(self) -> None:
        monitor = self._app_monitor
        handler = self._app_monitor_handler
        self._app_monitor = None
        self._app_monitor_handler = None
        if monitor is None or handler is None:
            return
        with suppress(AttributeError, TypeError, ValueError, GLib.Error):
            monitor.disconnect(handler)

    def _sync_directory_monitors(self) -> None:
        try:
            wanted = set(discovery.unique_paths(self._desktop_directories_source()))
            wanted.update(self._executable_registration.directories())
            wanted.update(self._state.diagnostic.steam_directories)
        except Exception as exc:
            log.bind(action="list_desktop_directories").warning(
                "Could not list desktop application directories: %s",
                exc,
            )
            return

        for path in tuple(self._directory_monitors):
            if path not in wanted:
                self._cancel_directory_monitor(path)

        for path in sorted(wanted, key=str):
            if path in self._directory_monitors:
                continue
            try:
                monitor = self._directory_monitor_factory(path)
                handler = monitor.connect(
                    "changed",
                    self._on_registry_changed,
                )
            except Exception as exc:
                log.bind(
                    action="monitor_desktop_directory",
                    path=str(path),
                ).warning(
                    "Could not monitor desktop application directory: %s",
                    exc,
                )
                continue
            self._directory_monitors[path] = (monitor, handler)

    def _cancel_directory_monitor(self, path: Path) -> None:
        monitor, handler = self._directory_monitors.pop(path)
        if handler is not None:
            with suppress(
                AttributeError,
                TypeError,
                ValueError,
                GLib.Error,
            ):
                monitor.disconnect(handler)
        with suppress(AttributeError, TypeError, ValueError, GLib.Error):
            monitor.cancel()

    def _cancel_directory_monitors(self) -> None:
        for path in tuple(self._directory_monitors):
            self._cancel_directory_monitor(path)

    def _on_registry_changed(self, *_args: object) -> None:
        if self._metadata is not None:
            self._metadata.invalidate()
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
        state.initial_discovery_pending,
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
    initial_discovery_pending: bool = False,
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
        initial_discovery_pending=initial_discovery_pending,
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


def _monitor_directory(path: Path) -> object:
    file = Gio.File.new_for_path(str(path))
    return file.monitor_directory(Gio.FileMonitorFlags.NONE, None)


__all__ = [
    "DEFAULT_DEBOUNCE_MS",
    "MAX_CONTENT_HANDLER_TOKENS",
    "ApplicationRegistry",
    "RegistryListener",
]
