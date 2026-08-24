"""Authoritative, immutable snapshots of installed application metadata."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import get_ident
from types import MappingProxyType
from typing import Any

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger, with_context

from . import discovery
from . import entries as desktop_entries
from .types import (
    ApplicationInfo,
    TransientApplicationInfo,
)

DEFAULT_DEBOUNCE_MS = 150
MAX_CONTENT_HANDLER_TOKENS = 64

RegistryListener = Callable[[], None]

log = with_context(get_logger(name="application_registry"))


@dataclass(frozen=True, slots=True)
class _RegistryState:
    generation: int
    handle_epoch: int
    applications_by_id: Mapping[str, ApplicationInfo]
    visible: tuple[ApplicationInfo, ...]
    wm_class_index: Mapping[str, tuple[ApplicationInfo, ...]]
    desktop_file_index: Mapping[Path, ApplicationInfo]
    gio_handles: Mapping[str, object]
    unidentified: tuple[TransientApplicationInfo, ...]
    unidentified_gio_handles: Mapping[str, object]


def _empty_state() -> _RegistryState:
    return _RegistryState(
        generation=0,
        handle_epoch=0,
        applications_by_id=MappingProxyType({}),
        visible=(),
        wm_class_index=MappingProxyType({}),
        desktop_file_index=MappingProxyType({}),
        gio_handles=MappingProxyType({}),
        unidentified=(),
        unidentified_gio_handles=MappingProxyType({}),
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
            discovery.default_gio_applications
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
        self._content_gio_handles: dict[str, object] = {}

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

    def resolve_by_desktop_file(self, path: Path) -> ApplicationInfo | None:
        """Resolve an application by its exact or canonical desktop-file path."""
        for key in discovery.desktop_file_keys(path):
            application = self._state.desktop_file_index.get(key)
            if application is not None:
                return application
        return None

    def _add_listener(self, callback: RegistryListener) -> None:
        """Register a listener once."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def _remove_listener(self, callback: RegistryListener) -> None:
        """Remove a listener if currently registered."""
        with suppress(ValueError):
            self._listeners.remove(callback)

    def subscribe(self, callback: RegistryListener) -> Callable[[], None]:
        """Register a listener and return an idempotent unsubscribe."""
        self._add_listener(callback)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed
            if not subscribed:
                return
            subscribed = False
            self._remove_listener(callback)

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
                visible=built.visible,
                wm_class_index=built.wm_class_index,
                desktop_file_index=built.desktop_file_index,
                gio_handles=built.gio_handles,
                unidentified=built.unidentified,
                unidentified_gio_handles=built.unidentified_gio_handles,
            )
            self._notify_listeners()
        else:
            self._state = _RegistryState(
                generation=current.generation,
                handle_epoch=handle_epoch,
                applications_by_id=current.applications_by_id,
                visible=current.visible,
                wm_class_index=current.wm_class_index,
                desktop_file_index=current.desktop_file_index,
                gio_handles=built.gio_handles,
                unidentified=built.unidentified,
                unidentified_gio_handles=built.unidentified_gio_handles,
            )
        self._content_gio_handles.clear()

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

    def _default_listing_for_content_type(
        self,
        content_type: str,
    ) -> ApplicationInfo | TransientApplicationInfo | None:
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
    ) -> ApplicationInfo | TransientApplicationInfo | None:
        """Select a visible handler using desktop association precedence.

        Defaults are considered for every content type first, followed by
        recommended handlers for every type, and finally the complete handler
        lists. This preserves the established media-launcher fallback order while
        keeping Gio association policy inside the registry.
        """
        self._assert_owner_thread("preferred_listing_for_content_types")
        ordered_types = tuple(content_types)
        for content_type in ordered_types:
            listing = self._default_listing_for_content_type(content_type)
            if listing is not None:
                return listing
        for lookup in (
            self._recommended_listings_for_content_type,
            self._all_listings_for_content_type,
        ):
            for content_type in ordered_types:
                listings = lookup(content_type)
                if listings:
                    return listings[0]
        return None

    def _listings_for_content_type(
        self,
        *,
        content_type: str,
        lookup_name: str,
        action: str,
    ) -> tuple[ApplicationInfo | TransientApplicationInfo, ...]:
        """Convert one Gio association result into canonical visible listings."""
        self._assert_owner_thread(action)
        lookup = getattr(Gio.AppInfo, lookup_name, None)
        if not callable(lookup):
            return ()
        try:
            app_infos = lookup(content_type)
        except Exception as exc:
            log.bind(
                action=action,
                content_type=content_type,
            ).debug(
                "Failed to list application handlers: %s",
                exc,
            )
            return ()

        result: list[ApplicationInfo | TransientApplicationInfo] = []
        seen_registered: set[str] = set()
        seen_unregistered: set[tuple[str, object]] = set()
        retained_count = 0
        retained_limit = max(1, MAX_CONTENT_HANDLER_TOKENS)
        for app_info in app_infos or ():
            should_show = discovery.safe_call(app_info, "should_show")
            if should_show is False or bool(
                discovery.safe_call(app_info, "get_is_hidden")
            ):
                continue
            registered = self._registered_application_for_content_type_result(app_info)
            if registered is not None:
                if not registered.visible or registered.desktop_id in seen_registered:
                    continue
                seen_registered.add(registered.desktop_id)
                result.append(registered)
                continue

            if bool(discovery.safe_call(app_info, "get_nodisplay")):
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
        return self._content_gio_handles.get(listing_key)

    def _discover(self, *, handle_epoch: int) -> _RegistryState:
        result = discovery.discover(
            application_source=self._application_source,
            desktop_directories_source=self._desktop_directories_source,
            desktop_app_info_from_filename=self._desktop_app_info_from_filename,
            handle_epoch=handle_epoch,
        )
        return _build_state(
            handle_epoch=handle_epoch,
            applications=result.applications,
            handles=result.handles,
            unidentified=result.transient,
            unidentified_handles=result.transient_handles,
            presentation_order=result.presentation_order,
        )

    def _application_for_content_type_result(
        self,
        app_info: object | None,
    ) -> ApplicationInfo | None:
        if app_info is None:
            return None
        registered = self._registered_application_for_content_type_result(app_info)
        if registered is not None:
            return registered

        desktop_id = discovery.desktop_id(app_info)
        filename = discovery.source_text(discovery.safe_call(app_info, "get_filename"))
        path = Path(filename).expanduser() if filename else None

        if not desktop_id:
            return None
        facts = discovery.file_facts(path) if path is not None else None
        if facts is not None and (not facts.is_application or facts.hidden):
            return None
        return discovery.application_from_gio(
            desktop_id=desktop_id,
            app_info=app_info,
            fallback_path=path,
            fallback_facts=facts,
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
        if app_info is None:
            return None
        registered = self._registered_application_for_content_type_result(app_info)
        if registered is not None:
            return registered
        return self._retain_content_listing(app_info)

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
            wanted = set(discovery.unique_paths(self._desktop_directories_source()))
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


def _state_content(state: _RegistryState) -> tuple[object, ...]:
    return (
        state.applications_by_id,
        state.visible,
        state.wm_class_index,
        state.desktop_file_index,
        state.unidentified,
    )


def _build_state(
    *,
    handle_epoch: int,
    applications: Iterable[ApplicationInfo],
    handles: Mapping[str, object],
    unidentified: Iterable[TransientApplicationInfo],
    unidentified_handles: Mapping[str, object],
    presentation_order: Iterable[str],
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
