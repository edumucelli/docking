"""Authoritative, immutable snapshots of installed application metadata."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import get_ident
from types import MappingProxyType
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger, with_context

from . import entries as desktop_entries
from .types import (
    ActionSource,
    ApplicationAction,
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)

DEFAULT_DEBOUNCE_MS = 150
MAX_CONTENT_HANDLER_TOKENS = 64

RegistryListener = Callable[[], None]

log = with_context(get_logger(name="application_registry"))


@dataclass(frozen=True, slots=True, kw_only=True)
class UnidentifiedApplicationListing:
    """Transient Gio metadata addressed by an opaque launch token."""

    listing_key: str = field(compare=False)
    name: str
    categories: str
    icon_name: str
    desktop_file: Path | None
    exec_line: str = ""
    description: str = ""
    generic_name: str = ""


@dataclass(frozen=True, slots=True)
class _FileAction:
    action_id: str
    name: str
    exec_line: str


@dataclass(frozen=True, slots=True)
class _FileFacts:
    is_application: bool
    hidden: bool
    no_display: bool
    generated: bool
    name: str
    declared_icon: str
    startup_wm_class: str
    exec_line: str
    generic_name: str
    description: str
    categories_raw: str
    keywords: tuple[str, ...]
    actions: tuple[_FileAction, ...]


@dataclass(frozen=True, slots=True)
class _RegistryState:
    generation: int
    handle_epoch: int
    applications_by_id: Mapping[str, ApplicationInfo]
    resolvable: tuple[ApplicationInfo, ...]
    visible: tuple[ApplicationInfo, ...]
    wm_class_index: Mapping[str, tuple[ApplicationInfo, ...]]
    executable_path_index: Mapping[Path, tuple[ApplicationInfo, ...]]
    desktop_file_index: Mapping[Path, ApplicationInfo]
    gio_handles: Mapping[str, object]
    unidentified: tuple[UnidentifiedApplicationListing, ...]
    unidentified_gio_handles: Mapping[str, object]
    content_gio_handles: Mapping[str, object]


def _empty_state() -> _RegistryState:
    return _RegistryState(
        generation=0,
        handle_epoch=0,
        applications_by_id=MappingProxyType({}),
        resolvable=(),
        visible=(),
        wm_class_index=MappingProxyType({}),
        executable_path_index=MappingProxyType({}),
        desktop_file_index=MappingProxyType({}),
        gio_handles=MappingProxyType({}),
        unidentified=(),
        unidentified_gio_handles=MappingProxyType({}),
        content_gio_handles=MappingProxyType({}),
    )


class ApplicationRegistry:
    """Discover applications and publish complete immutable generations."""

    def __init__(
        self,
        *,
        application_source: Callable[[], Iterable[object]] | None = None,
        desktop_directories_source: Callable[[], Iterable[Path]] | None = None,
    ) -> None:
        self._owner_thread_id = get_ident()
        self._application_source = (
            _default_gio_applications
            if application_source is None
            else application_source
        )
        self._desktop_directories_source = (
            desktop_directories_source or desktop_entries.desktop_dirs
        )
        self._desktop_app_info_from_filename = Gio.DesktopAppInfo.new_from_filename
        self._app_monitor_factory = Gio.AppInfoMonitor.get
        self._directory_monitor_factory = _monitor_directory
        self._schedule_timeout = GLib.timeout_add
        self._cancel_timeout = GLib.source_remove
        self._debounce_ms = DEFAULT_DEBOUNCE_MS

        self._state = _empty_state()
        self._loaded = False
        self._listeners: list[RegistryListener] = []
        self._started = False
        self._lifecycle_token = 0
        self._content_token_serial = 0

        self._app_monitor: object | None = None
        self._app_monitor_handler: object | None = None
        self._directory_monitors: dict[Path, tuple[object, object | None]] = {}
        self._debounce_source_id: int | None = None

    @property
    def generation(self) -> int:
        """Return the current immutable generation number."""
        return self._state.generation

    @property
    def started(self) -> bool:
        """Return whether application monitoring is active."""
        return self._started

    @property
    def applications(self) -> tuple[ApplicationInfo, ...]:
        """Compatibility alias for the visible application snapshot."""
        return self._state.visible

    @property
    def applications_by_id(self) -> Mapping[str, ApplicationInfo]:
        """Return the immutable resolver map for the current generation."""
        return self._state.applications_by_id

    def snapshot(self) -> tuple[ApplicationInfo, ...]:
        """Return visible applications in stable presentation order."""
        return self._state.visible

    def resolvable_snapshot(self) -> tuple[ApplicationInfo, ...]:
        """Return all resolvable records in source-precedence order."""
        return self._state.resolvable

    def unidentified_snapshot(
        self,
    ) -> tuple[UnidentifiedApplicationListing, ...]:
        """Return visible Gio listings that cannot participate in ID lookup."""
        return self._state.unidentified

    def get(self, desktop_id: str) -> ApplicationInfo | None:
        """Read one canonical record without consulting Gio."""
        return self._state.applications_by_id.get(desktop_id)

    def resolve(
        self,
        desktop_id: str,
        *,
        log_failures: bool = True,
    ) -> ApplicationInfo | None:
        """Resolve a desktop ID from the current immutable generation."""
        application = self._state.applications_by_id.get(desktop_id)
        if application is None and log_failures:
            log.bind(desktop_id=desktop_id, action="resolve").debug(
                "Desktop application is not present in the registry"
            )
        return application

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

    def resolve_all_by_executable_path(
        self,
        path: Path,
    ) -> tuple[ApplicationInfo, ...]:
        """Return every record with an exact canonical executable path."""
        expanded = path.expanduser()
        direct = self._state.executable_path_index.get(expanded)
        if direct is not None:
            return direct
        try:
            canonical = expanded.resolve(strict=True)
        except (OSError, RuntimeError):
            return ()
        return self._state.executable_path_index.get(canonical, ())

    def resolve_by_executable_path(
        self,
        executable_path: Path,
    ) -> ApplicationInfo | None:
        """Compatibility singular executable-path resolver."""
        matches = self.resolve_all_by_executable_path(executable_path)
        return matches[0] if matches else None

    def resolve_by_desktop_file(self, path: Path) -> ApplicationInfo | None:
        """Resolve an application by its exact or canonical desktop-file path."""
        for key in _desktop_file_keys(path):
            application = self._state.desktop_file_index.get(key)
            if application is not None:
                return application
        return None

    def add_listener(self, callback: RegistryListener) -> None:
        """Register a listener once."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: RegistryListener) -> None:
        """Remove a listener if currently registered."""
        with suppress(ValueError):
            self._listeners.remove(callback)

    def subscribe(self, callback: RegistryListener) -> Callable[[], None]:
        """Register a listener and return an idempotent unsubscribe."""
        self.add_listener(callback)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if not subscribed:
                return
            subscribed = False
            self.remove_listener(callback)

        return unsubscribe

    def refresh(self) -> bool:
        """Build and atomically publish a complete generation."""
        self._assert_owner_thread("refresh")
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
            self._state = _RegistryState(
                generation=current.generation + 1,
                handle_epoch=handle_epoch,
                applications_by_id=built.applications_by_id,
                resolvable=built.resolvable,
                visible=built.visible,
                wm_class_index=built.wm_class_index,
                executable_path_index=built.executable_path_index,
                desktop_file_index=built.desktop_file_index,
                gio_handles=built.gio_handles,
                unidentified=built.unidentified,
                unidentified_gio_handles=built.unidentified_gio_handles,
                content_gio_handles=MappingProxyType({}),
            )
            self._notify_listeners()
        else:
            self._state = _RegistryState(
                generation=current.generation,
                handle_epoch=handle_epoch,
                applications_by_id=current.applications_by_id,
                resolvable=current.resolvable,
                visible=current.visible,
                wm_class_index=current.wm_class_index,
                executable_path_index=current.executable_path_index,
                desktop_file_index=current.desktop_file_index,
                gio_handles=built.gio_handles,
                unidentified=built.unidentified,
                unidentified_gio_handles=built.unidentified_gio_handles,
                content_gio_handles=MappingProxyType({}),
            )

        if self._started:
            self._sync_directory_monitors()
        return changed

    def start(self) -> None:
        """Start monitors and synchronously populate the registry."""
        self._assert_owner_thread("start")
        if self._started:
            return
        self._started = True
        self._lifecycle_token += 1
        self._connect_app_monitor()
        self._sync_directory_monitors()
        self.refresh()

    def stop(self) -> None:
        """Stop monitors while retaining the last immutable generation."""
        self._assert_owner_thread("stop")
        if not self._started:
            return
        self._started = False
        self._lifecycle_token += 1
        self._cancel_pending_refresh()
        self._disconnect_app_monitor()
        self._cancel_directory_monitors()

    def default_for_content_type(
        self,
        content_type: str,
    ) -> ApplicationInfo | None:
        """Resolve the desktop's default handler on the calling main thread."""
        self._assert_owner_thread("default_for_content_type")
        try:
            app_info = Gio.AppInfo.get_default_for_type(content_type, False)
        except Exception as exc:
            log.bind(
                action="default_for_content_type",
                content_type=content_type,
            ).debug(
                "Failed to resolve default application: %s",
                exc,
            )
            return None
        return self._application_for_content_type_result(app_info)

    def recommended_for_content_type(
        self,
        content_type: str,
    ) -> tuple[ApplicationInfo, ...]:
        """Resolve visible recommended handlers on the calling main thread."""
        self._assert_owner_thread("recommended_for_content_type")
        lookup = getattr(Gio.AppInfo, "get_recommended_for_type", None)
        if not callable(lookup):
            lookup = getattr(Gio.AppInfo, "get_all_for_type", None)
        if not callable(lookup):
            return ()
        try:
            app_infos = lookup(content_type)
        except Exception as exc:
            log.bind(
                action="recommended_for_content_type",
                content_type=content_type,
            ).debug(
                "Failed to list recommended applications: %s",
                exc,
            )
            return ()

        result: list[ApplicationInfo] = []
        seen: set[str] = set()
        for app_info in app_infos or ():
            should_show = _safe_call(app_info, "should_show")
            if should_show is False:
                continue
            application = self._application_for_content_type_result(app_info)
            if (
                application is None
                or not application.visible
                or application.desktop_id in seen
            ):
                continue
            seen.add(application.desktop_id)
            result.append(application)
        return tuple(result)

    def default_listing_for_content_type(
        self,
        content_type: str,
    ) -> ApplicationInfo | UnidentifiedApplicationListing | None:
        """Return a launchable default handler with any Gio handle retained."""
        self._assert_owner_thread("default_listing_for_content_type")
        try:
            app_info = Gio.AppInfo.get_default_for_type(content_type, False)
        except Exception as exc:
            log.bind(
                action="default_listing_for_content_type",
                content_type=content_type,
            ).debug(
                "Failed to resolve default application listing: %s",
                exc,
            )
            return None
        return self._listing_for_content_type_result(app_info)

    def recommended_listings_for_content_type(
        self,
        content_type: str,
    ) -> tuple[ApplicationInfo | UnidentifiedApplicationListing, ...]:
        """Return visible launchable handlers with private Gio handles retained."""
        self._assert_owner_thread("recommended_listings_for_content_type")
        lookup = getattr(Gio.AppInfo, "get_recommended_for_type", None)
        if not callable(lookup):
            lookup = getattr(Gio.AppInfo, "get_all_for_type", None)
        if not callable(lookup):
            return ()
        try:
            app_infos = lookup(content_type)
        except Exception as exc:
            log.bind(
                action="recommended_listings_for_content_type",
                content_type=content_type,
            ).debug(
                "Failed to list recommended application listings: %s",
                exc,
            )
            return ()

        result: list[ApplicationInfo | UnidentifiedApplicationListing] = []
        seen_registered: set[str] = set()
        seen_unregistered: set[tuple[str, object]] = set()
        retained_count = 0
        retained_limit = max(1, MAX_CONTENT_HANDLER_TOKENS)
        for app_info in app_infos or ():
            should_show = _safe_call(app_info, "should_show")
            if should_show is False or _safe_bool_call(app_info, "get_is_hidden"):
                continue
            registered = self._registered_application_for_content_type_result(app_info)
            if registered is not None:
                if not registered.visible or registered.desktop_id in seen_registered:
                    continue
                seen_registered.add(registered.desktop_id)
                result.append(registered)
                continue

            if _safe_bool_call(app_info, "get_nodisplay"):
                continue
            desktop_id = _desktop_id_from_app_info(app_info)
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

    def _gio_handle_for(self, desktop_id: str) -> object | None:
        """Return a generation-owned Gio handle for future main-thread services."""
        self._assert_owner_thread("_gio_handle_for")
        return self._state.gio_handles.get(desktop_id)

    def _gio_handle_for_unidentified(self, listing_key: str) -> object | None:
        """Return the private Gio handle for an opaque launchable listing."""
        self._assert_owner_thread("_gio_handle_for_unidentified")
        handle = self._state.unidentified_gio_handles.get(listing_key)
        if handle is not None:
            return handle
        return self._state.content_gio_handles.get(listing_key)

    def _discover(self, *, handle_epoch: int) -> _RegistryState:
        gio_entries = tuple(self._application_source())
        directories = _unique_paths(self._desktop_directories_source())
        file_winners, file_order = _discover_desktop_files(directories)

        gio_by_id: dict[str, object] = {}
        gio_order: list[str] = []
        unidentified: list[UnidentifiedApplicationListing] = []
        unidentified_handles: dict[str, object] = {}
        for source_position, app_info in enumerate(gio_entries):
            desktop_id = _desktop_id_from_app_info(app_info)
            if not desktop_id:
                listing = _unidentified_listing(
                    app_info=app_info,
                    handle_epoch=handle_epoch,
                    source_position=source_position,
                )
                if listing is not None:
                    unidentified.append(listing)
                    unidentified_handles[listing.listing_key] = app_info
                continue
            if desktop_id in gio_by_id:
                continue
            gio_by_id[desktop_id] = app_info
            gio_order.append(desktop_id)

        applications: list[ApplicationInfo] = []
        handles: dict[str, object] = {}
        consumed_gio_ids: set[str] = set()

        for desktop_id in file_order:
            path = file_winners[desktop_id]
            facts = _file_facts(path)
            if facts is not None and (not facts.is_application or facts.hidden):
                consumed_gio_ids.add(desktop_id)
                continue

            app_info = gio_by_id.get(desktop_id)
            if app_info is None:
                app_info = self._app_info_from_filename(path)
            else:
                consumed_gio_ids.add(desktop_id)

            if app_info is None:
                if facts is None or not facts.is_application:
                    continue
                application = _application_from_file(
                    desktop_id=desktop_id,
                    path=path,
                    facts=facts,
                )
            else:
                application = _application_from_gio(
                    desktop_id=desktop_id,
                    app_info=app_info,
                    fallback_path=path,
                    fallback_facts=facts,
                )
                if application is not None:
                    handles[desktop_id] = app_info
            if application is not None:
                applications.append(application)

        for desktop_id in gio_order:
            if desktop_id in consumed_gio_ids or desktop_id in file_winners:
                continue
            app_info = gio_by_id[desktop_id]
            application = _application_from_gio(
                desktop_id=desktop_id,
                app_info=app_info,
                fallback_path=None,
                fallback_facts=None,
            )
            if application is None:
                continue
            applications.append(application)
            handles[desktop_id] = app_info

        return _build_state(
            handle_epoch=handle_epoch,
            applications=applications,
            handles=handles,
            unidentified=unidentified,
            unidentified_handles=unidentified_handles,
        )

    def _app_info_from_filename(self, path: Path) -> object | None:
        try:
            return self._desktop_app_info_from_filename(str(path))
        except Exception as exc:
            log.bind(
                action="load_desktop_app_info",
                path=str(path),
            ).debug(
                "Gio could not load desktop file: %s",
                exc,
            )
            return None

    def _application_for_content_type_result(
        self,
        app_info: object | None,
    ) -> ApplicationInfo | None:
        if app_info is None:
            return None
        registered = self._registered_application_for_content_type_result(app_info)
        if registered is not None:
            return registered

        desktop_id = _desktop_id_from_app_info(app_info)
        filename = _source_text(_safe_call(app_info, "get_filename"))
        path = Path(filename).expanduser() if filename else None

        if not desktop_id:
            return None
        facts = _file_facts(path) if path is not None else None
        if facts is not None and (not facts.is_application or facts.hidden):
            return None
        return _application_from_gio(
            desktop_id=desktop_id,
            app_info=app_info,
            fallback_path=path,
            fallback_facts=facts,
        )

    def _registered_application_for_content_type_result(
        self,
        app_info: object,
    ) -> ApplicationInfo | None:
        desktop_id = _desktop_id_from_app_info(app_info)
        if desktop_id:
            application = self._state.applications_by_id.get(desktop_id)
            if application is not None:
                return application

        filename = _source_text(_safe_call(app_info, "get_filename"))
        if not filename:
            return None
        path = Path(filename).expanduser()
        for key in _desktop_file_keys(path):
            application = self._state.desktop_file_index.get(key)
            if application is not None:
                return application
        return None

    def _listing_for_content_type_result(
        self,
        app_info: object | None,
    ) -> ApplicationInfo | UnidentifiedApplicationListing | None:
        if app_info is None:
            return None
        registered = self._registered_application_for_content_type_result(app_info)
        if registered is not None:
            return registered
        return self._retain_content_listing(app_info)

    def _retain_content_listing(
        self,
        app_info: object,
    ) -> UnidentifiedApplicationListing:
        self._content_token_serial += 1
        state = self._state
        listing_key = f"gio-content:{state.handle_epoch}:{self._content_token_serial}"
        listing = _application_listing_from_gio(
            app_info=app_info,
            listing_key=listing_key,
            require_visible=False,
        )
        if listing is None:
            raise AssertionError("unfiltered Gio listing unexpectedly missing")

        handles = dict(state.content_gio_handles)
        handles[listing_key] = app_info
        while len(handles) > max(1, MAX_CONTENT_HANDLER_TOKENS):
            handles.pop(next(iter(handles)))
        self._state = replace(
            state,
            content_gio_handles=MappingProxyType(handles),
        )
        return listing

    def _assert_owner_thread(self, operation: str) -> None:
        if get_ident() == self._owner_thread_id:
            return
        raise RuntimeError(
            f"ApplicationRegistry.{operation} must run on its owner thread"
        )

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
            wanted = set(_unique_paths(self._desktop_directories_source()))
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


def _default_gio_applications() -> tuple[object, ...]:
    """Return only desktop-backed entries from Gio's broad app-info list."""
    return tuple(
        app_info
        for app_info in Gio.AppInfo.get_all()
        if _is_gio_desktop_app_info(app_info)
    )


def _is_gio_desktop_app_info(app_info: object) -> bool:
    return isinstance(app_info, Gio.DesktopAppInfo)


def _unidentified_listing(
    *,
    app_info: object,
    handle_epoch: int,
    source_position: int,
) -> UnidentifiedApplicationListing | None:
    return _application_listing_from_gio(
        app_info=app_info,
        listing_key=f"gio-idless:{handle_epoch}:{source_position}",
        require_visible=True,
    )


def _application_listing_from_gio(
    *,
    app_info: object,
    listing_key: str,
    require_visible: bool,
) -> UnidentifiedApplicationListing | None:
    if require_visible and (
        _safe_bool_call(app_info, "get_is_hidden")
        or _safe_bool_call(app_info, "get_nodisplay")
    ):
        return None
    icon = _safe_call(app_info, "get_icon")
    filename = _source_text(_safe_call(app_info, "get_filename"))
    desktop_id = _desktop_id_from_app_info(app_info)
    return UnidentifiedApplicationListing(
        listing_key=listing_key,
        name=(
            _source_text(_safe_call(app_info, "get_display_name"))
            or desktop_id
            or "Unknown"
        ),
        categories=_source_text(_safe_call(app_info, "get_categories")),
        icon_name=_source_text(_safe_call(icon, "to_string")),
        desktop_file=Path(filename).expanduser() if filename else None,
        exec_line=_source_text(_safe_call(app_info, "get_commandline")),
        description=_source_text(_safe_call(app_info, "get_description")),
        generic_name=_source_text(_safe_call(app_info, "get_generic_name")),
    )


def _state_content(state: _RegistryState) -> tuple[object, ...]:
    return (
        state.applications_by_id,
        state.resolvable,
        state.visible,
        state.wm_class_index,
        state.executable_path_index,
        state.desktop_file_index,
        state.unidentified,
    )


def _build_state(
    *,
    handle_epoch: int,
    applications: Iterable[ApplicationInfo],
    handles: Mapping[str, object],
    unidentified: Iterable[UnidentifiedApplicationListing],
    unidentified_handles: Mapping[str, object],
) -> _RegistryState:
    applications_by_id: dict[str, ApplicationInfo] = {}
    resolvable: list[ApplicationInfo] = []
    for application in applications:
        if application.desktop_id in applications_by_id:
            continue
        applications_by_id[application.desktop_id] = application
        resolvable.append(application)

    visible = tuple(
        sorted(
            (application for application in resolvable if application.visible),
            key=lambda application: (
                _normalize_search_text(application.name),
                application.desktop_id.casefold(),
            ),
        )
    )

    alias_lists: dict[str, list[ApplicationInfo]] = {}
    executable_lists: dict[Path, list[ApplicationInfo]] = {}
    desktop_file_index: dict[Path, ApplicationInfo] = {}
    for application in resolvable:
        for alias in application.aliases:
            candidates = alias_lists.setdefault(alias, [])
            if all(
                candidate.desktop_id != application.desktop_id
                for candidate in candidates
            ):
                candidates.append(application)
        if application.executable_path is not None:
            executable_lists.setdefault(
                application.executable_path,
                [],
            ).append(application)
        if application.desktop_file is not None:
            for key in _desktop_file_keys(application.desktop_file):
                desktop_file_index.setdefault(key, application)

    unidentified_snapshot = tuple(unidentified)
    listing_keys = {application.listing_key for application in unidentified_snapshot}

    return _RegistryState(
        generation=0,
        handle_epoch=handle_epoch,
        applications_by_id=MappingProxyType(applications_by_id),
        resolvable=tuple(resolvable),
        visible=visible,
        wm_class_index=_freeze_plural_index(alias_lists),
        executable_path_index=_freeze_plural_index(executable_lists),
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
        content_gio_handles=MappingProxyType({}),
    )


def _freeze_plural_index(
    values: Mapping[Any, Iterable[ApplicationInfo]],
) -> Mapping[Any, tuple[ApplicationInfo, ...]]:
    return MappingProxyType(
        {key: tuple(candidates) for key, candidates in values.items()}
    )


def _desktop_file_keys(path: Path) -> tuple[Path, ...]:
    """Return exact-expanded and canonical keys without changing source rank."""
    expanded = path.expanduser()
    keys = [expanded]
    try:
        canonical = expanded.resolve(strict=False)
    except (OSError, RuntimeError):
        canonical = None
    if canonical is not None and canonical != expanded:
        keys.append(canonical)
    return tuple(keys)


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for value in paths:
        path = Path(value).expanduser()
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return tuple(result)


def _discover_desktop_files(
    directories: Iterable[Path],
) -> tuple[dict[str, Path], list[str]]:
    winners: dict[str, Path] = {}
    order: list[str] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.rglob(f"*{desktop_entries.DESKTOP_SUFFIX}"):
            if not path.is_file():
                continue
            desktop_id = path.relative_to(directory).as_posix()
            if desktop_id in winners:
                continue
            winners[desktop_id] = path
            order.append(desktop_id)
    return winners, order


def _file_facts(path: Path | None) -> _FileFacts | None:
    if path is None:
        return None
    key_file = desktop_entries.load_desktop_key_file(path)
    if key_file is None:
        return None

    is_application = (
        desktop_entries.desktop_entry_string(key_file, "Type") == "Application"
    )
    hidden = desktop_entries.desktop_entry_bool(key_file, "Hidden")
    no_display = desktop_entries.desktop_entry_bool(
        key_file,
        "NoDisplay",
    )
    action_values: list[_FileAction] = []
    try:
        action_ids = key_file.get_string_list("Desktop Entry", "Actions")
    except Exception:
        action_ids = ()
    for raw_action_id in action_ids:
        action_id = _clean_text(raw_action_id)
        if not action_id:
            continue
        group = f"Desktop Action {action_id}"
        try:
            name = _plain_text(key_file.get_locale_string(group, "Name", None))
        except Exception:
            name = ""
        if not name:
            continue
        try:
            exec_line = _plain_text(key_file.get_string(group, "Exec"))
        except Exception:
            exec_line = ""
        action_values.append(
            _FileAction(
                action_id=action_id,
                name=name,
                exec_line=exec_line,
            )
        )

    return _FileFacts(
        is_application=is_application,
        hidden=hidden,
        no_display=no_display,
        generated=desktop_entries.desktop_entry_bool(
            key_file,
            desktop_entries.GENERATED_MARKER_KEY,
        ),
        name=desktop_entries.desktop_entry_locale_string(
            key_file,
            "Name",
        ),
        declared_icon=desktop_entries.desktop_entry_string(
            key_file,
            "Icon",
        ),
        startup_wm_class=desktop_entries.desktop_entry_string(
            key_file,
            "StartupWMClass",
        ),
        exec_line=desktop_entries.desktop_entry_string(
            key_file,
            "Exec",
        ),
        generic_name=desktop_entries.desktop_entry_locale_string(
            key_file,
            "GenericName",
        ),
        description=desktop_entries.desktop_entry_locale_string(
            key_file,
            "Comment",
        ),
        categories_raw=desktop_entries.desktop_entry_string(
            key_file,
            "Categories",
        ),
        keywords=_normalise_values(
            desktop_entries.desktop_entry_locale_string(
                key_file,
                "Keywords",
            )
        ),
        actions=tuple(action_values),
    )


def _application_from_file(
    *,
    desktop_id: str,
    path: Path,
    facts: _FileFacts,
) -> ApplicationInfo:
    exec_line = facts.exec_line
    wm_class = _wm_class(
        desktop_id=desktop_id,
        startup_wm_class=facts.startup_wm_class,
        exec_line=exec_line,
    )
    name = facts.name or desktop_id
    return _make_application(
        desktop_id=desktop_id,
        name=name,
        declared_icon=facts.declared_icon,
        wm_class=wm_class,
        exec_line=exec_line,
        origin=_origin(
            desktop_id=desktop_id,
            generated=facts.generated,
        ),
        location=_location(path),
        desktop_file=path,
        visible=not facts.no_display,
        has_gio_source=False,
        generic_name=facts.generic_name,
        description=_file_search_description(facts),
        categories_raw=facts.categories_raw,
        keywords=facts.keywords,
        actions=_merge_actions((), facts.actions),
    )


def _application_from_gio(
    *,
    desktop_id: str,
    app_info: object,
    fallback_path: Path | None,
    fallback_facts: _FileFacts | None,
) -> ApplicationInfo | None:
    if _safe_bool_call(app_info, "get_is_hidden"):
        return None

    filename = _source_text(_safe_call(app_info, "get_filename"))
    desktop_file = Path(filename).expanduser() if filename else fallback_path
    file_facts = fallback_facts
    if desktop_file is not None and desktop_file != fallback_path:
        file_facts = _file_facts(desktop_file)
    if file_facts is not None and (not file_facts.is_application or file_facts.hidden):
        return None

    exec_line = _source_text(_safe_call(app_info, "get_commandline"))
    startup_wm_class = _source_text(_safe_call(app_info, "get_startup_wm_class"))
    icon = _safe_call(app_info, "get_icon")
    declared_icon = _source_text(_safe_call(icon, "to_string"))
    name = _source_text(_safe_call(app_info, "get_display_name")) or desktop_id
    generic_name = _source_text(_safe_call(app_info, "get_generic_name"))
    if not _clean_text(generic_name) and file_facts is not None:
        generic_name = file_facts.generic_name

    description = _source_text(_safe_call(app_info, "get_description"))
    if not _clean_text(description) and file_facts is not None:
        description = file_facts.description
        if not _clean_text(description):
            description = file_facts.generic_name

    raw_categories = _safe_call(app_info, "get_categories")
    categories_raw = _source_text(raw_categories) if raw_categories is not None else ""
    keywords = _normalise_values(_safe_call(app_info, "get_keywords"))
    if not keywords and file_facts is not None:
        keywords = file_facts.keywords

    file_visible = file_facts is None or not file_facts.no_display
    visible = not _safe_bool_call(app_info, "get_nodisplay") and file_visible
    generated = bool(file_facts and file_facts.generated)
    return _make_application(
        desktop_id=desktop_id,
        name=name,
        declared_icon=declared_icon,
        wm_class=_gio_wm_class(
            desktop_id=desktop_id,
            startup_wm_class=startup_wm_class,
            exec_line=exec_line,
        ),
        exec_line=exec_line,
        origin=_origin(
            desktop_id=desktop_id,
            generated=generated,
        ),
        location=_location(desktop_file),
        desktop_file=desktop_file,
        visible=visible,
        has_gio_source=True,
        generic_name=generic_name,
        description=description,
        categories_raw=categories_raw,
        keywords=keywords,
        actions=_merge_actions(
            _gio_actions(app_info),
            file_facts.actions if file_facts is not None else (),
        ),
    )


def _file_search_description(facts: _FileFacts) -> str:
    return facts.description if _clean_text(facts.description) else facts.generic_name


def _make_application(
    *,
    desktop_id: str,
    name: str,
    declared_icon: str,
    wm_class: str,
    exec_line: str,
    origin: ApplicationOrigin,
    location: ApplicationLocation,
    desktop_file: Path | None,
    visible: bool,
    has_gio_source: bool,
    generic_name: str,
    description: str,
    categories_raw: str,
    keywords: tuple[str, ...],
    actions: tuple[ApplicationAction, ...],
) -> ApplicationInfo:
    aliases = tuple(
        desktop_entries.match_aliases(
            desktop_id,
            wm_class,
            exec_line,
        )
    )
    return ApplicationInfo(
        desktop_id=desktop_id,
        name=name,
        declared_icon=declared_icon,
        wm_class=wm_class,
        exec_line=exec_line,
        origin=origin,
        location=location,
        desktop_file=desktop_file,
        executable_path=desktop_entries.executable_path_from_exec_line(exec_line),
        aliases=aliases,
        visible=visible,
        has_gio_source=has_gio_source,
        generic_name=generic_name,
        description=description,
        categories=_normalise_values(categories_raw),
        categories_raw=categories_raw,
        keywords=keywords,
        actions=actions,
    )


def _gio_actions(app_info: object) -> tuple[tuple[str, str], ...]:
    action_ids = _safe_call(app_info, "list_actions")
    if not action_ids:
        return ()
    result: list[tuple[str, str]] = []
    for raw_action_id in action_ids:
        action_id = _clean_text(raw_action_id)
        if not action_id:
            continue
        name = _clean_text(_safe_call(app_info, "get_action_name", action_id))
        if name:
            result.append((action_id, name))
    return tuple(result)


def _merge_actions(
    gio_actions: Iterable[tuple[str, str]],
    file_actions: Iterable[_FileAction],
) -> tuple[ApplicationAction, ...]:
    merged: dict[str, ApplicationAction] = {}
    for raw_action_id, raw_name in gio_actions:
        action_id = _clean_text(raw_action_id)
        name = _clean_text(raw_name)
        if not action_id or not name or action_id in merged:
            continue
        merged[action_id] = ApplicationAction(
            action_id=action_id,
            name=name,
            sources=frozenset({ActionSource.GIO}),
        )

    for file_action in file_actions:
        action_id = _clean_text(file_action.action_id)
        name = _clean_text(file_action.name)
        if not action_id or not name:
            continue
        existing = merged.get(action_id)
        if existing is None:
            merged[action_id] = ApplicationAction(
                action_id=action_id,
                name=name,
                sources=frozenset({ActionSource.DESKTOP_FILE}),
                file_exec_line=file_action.exec_line,
            )
            continue
        if ActionSource.DESKTOP_FILE in existing.sources:
            continue
        merged[action_id] = ApplicationAction(
            action_id=existing.action_id,
            name=existing.name,
            sources=existing.sources | {ActionSource.DESKTOP_FILE},
            file_exec_line=file_action.exec_line,
        )
    return tuple(merged.values())


def _desktop_id_from_app_info(app_info: object) -> str:
    return _plain_text(
        _safe_call(app_info, "get_id") or getattr(app_info, "desktop_id", "")
    )


def _wm_class(
    *,
    desktop_id: str,
    startup_wm_class: str,
    exec_line: str,
) -> str:
    wine_aliases = desktop_entries.wine_executable_aliases(exec_line)
    if startup_wm_class and startup_wm_class.lower() != "wine":
        return startup_wm_class
    if wine_aliases:
        return wine_aliases[0]
    executable = desktop_entries.normalized_exec_basename(exec_line)
    return executable or desktop_id.removesuffix(desktop_entries.DESKTOP_SUFFIX)


def _gio_wm_class(
    *,
    desktop_id: str,
    startup_wm_class: str,
    exec_line: str,
) -> str:
    wine_aliases = desktop_entries.wine_executable_aliases(exec_line)
    if startup_wm_class and startup_wm_class.lower() != "wine":
        return startup_wm_class
    if wine_aliases:
        return wine_aliases[0]
    executable = exec_line.split()[0] if exec_line else ""
    return (
        Path(executable).name
        if executable
        else desktop_id.removesuffix(desktop_entries.DESKTOP_SUFFIX)
    )


def _origin(
    *,
    desktop_id: str,
    generated: bool,
) -> ApplicationOrigin:
    if generated or desktop_id.startswith(desktop_entries.GENERATED_DESKTOP_PREFIX):
        return ApplicationOrigin.GENERATED
    return ApplicationOrigin.INSTALLED


def _location(path: Path | None) -> ApplicationLocation:
    if desktop_entries.is_host_desktop_file(path):
        return ApplicationLocation.HOST
    return ApplicationLocation.SANDBOX


def _normalise_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_values: Iterable[object] = value.split(";")
    else:
        try:
            raw_values = list(value)
        except TypeError:
            raw_values = (value,)

    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        clean = _clean_text(raw_value)
        key = _normalize_search_text(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return tuple(result)


def _normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.casefold().split())


def _plain_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _source_text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _safe_bool_call(target: object, method_name: str) -> bool:
    return bool(_safe_call(target, method_name))


def _safe_call(
    target: object | None,
    method_name: str,
    *args: object,
) -> Any:
    if target is None:
        return None
    method = getattr(target, method_name, None)
    if not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None


def _monitor_directory(path: Path) -> object:
    file = Gio.File.new_for_path(str(path))
    return file.monitor_directory(Gio.FileMonitorFlags.NONE, None)


__all__ = [
    "DEFAULT_DEBOUNCE_MS",
    "MAX_CONTENT_HANDLER_TOKENS",
    "ApplicationRegistry",
    "RegistryListener",
    "UnidentifiedApplicationListing",
]
