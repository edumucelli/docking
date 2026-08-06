"""Process-wide canonical application metadata registry.

The ``ApplicationRegistry`` is the single source of truth for installed
desktop-application metadata.  Discovery, parsing, monitoring, indexing,
and invalidation all live here.  Launching and matching are consumers
of the registry, not independent metadata repositories.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from docking.applets.apps import all_desktop_app_infos
from docking.log import get_logger, with_context
from docking.platform import desktop_entries
from docking.platform.applications.types import (
    ApplicationAction,
    ApplicationInfo,
    ApplicationOrigin,
)

DEFAULT_DEBOUNCE_MS = 150

log = with_context(get_logger(name="app_registry"))


def _origin_for_desktop_file(path: Path | None) -> ApplicationOrigin:
    if path is None:
        return ApplicationOrigin.INSTALLED
    if desktop_entries.is_host_desktop_file(path):
        return ApplicationOrigin.HOST
    try:
        for d in desktop_entries.desktop_dirs():
            try:
                path.relative_to(d)
            except ValueError:
                continue
            desktop_id = path.relative_to(d).as_posix()
            if desktop_id.startswith(desktop_entries.GENERATED_DESKTOP_PREFIX):
                return ApplicationOrigin.GENERATED
            break
    except (OSError, ValueError):
        pass
    return ApplicationOrigin.INSTALLED


def _build_info(
    *,
    desktop_id: str,
    name: str,
    icon_name: str,
    wm_class: str,
    exec_line: str,
    categories: str = "",
    description: str = "",
    generic_name: str = "",
    keywords: tuple[str, ...] = (),
    desktop_file: Path | None = None,
    actions: tuple[ApplicationAction, ...] = (),
) -> ApplicationInfo:
    executable_path = desktop_entries.executable_path_from_exec_line(exec_line)
    aliases = tuple(
        desktop_entries.desktop_match_aliases(
            desktop_entries.DesktopInfo(
                desktop_id=desktop_id,
                name=name,
                icon_name=icon_name,
                wm_class=wm_class,
                exec_line=exec_line,
            )
        )
    )
    origin = _origin_for_desktop_file(desktop_file)
    return ApplicationInfo(
        desktop_id=desktop_id,
        name=name,
        icon_name=icon_name,
        wm_class=wm_class,
        exec_line=exec_line,
        origin=origin,
        desktop_file=desktop_file,
        executable_path=executable_path,
        aliases=aliases,
        generic_name=generic_name,
        description=description,
        categories=categories,
        keywords=keywords,
        actions=actions,
    )


def _monitor_directory(path: Path) -> Gio.FileMonitor:
    f = Gio.File.new_for_path(str(path))
    return f.monitor_directory(Gio.FileMonitorFlags.NONE, None)


class ApplicationRegistry:
    """Process-wide canonical application metadata."""

    def __init__(self) -> None:
        self._application_source = all_desktop_app_infos
        self._desktop_directories_source = desktop_entries.desktop_dirs
        self._app_monitor_factory = Gio.AppInfoMonitor.get
        self._directory_monitor_factory = _monitor_directory
        self._schedule_timeout = GLib.timeout_add
        self._cancel_timeout = GLib.source_remove
        self._debounce_ms = DEFAULT_DEBOUNCE_MS
        self._installed_by_id: dict[str, ApplicationInfo] = {}
        self._ordered_snapshot: tuple[ApplicationInfo, ...] = ()
        self._alias_index: dict[str, list[str]] = {}
        self._executable_index: dict[Path, list[str]] = {}
        self._listeners: list[Callable[[], None]] = []
        self._generation = 0
        self._loaded = False
        self._started = False
        self._lifecycle_token = 0
        self._app_monitor: object | None = None
        self._app_monitor_handler: object | None = None
        self._directory_monitors: dict[Path, tuple[object, object | None]] = {}
        self._debounce_source_id: int | None = None

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._lifecycle_token += 1
        self._connect_app_monitor()
        self._sync_directory_monitors()
        self.refresh()

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._lifecycle_token += 1
        self._cancel_pending_refresh()
        self._disconnect_app_monitor()
        self._cancel_directory_monitors()

    def refresh(self) -> bool:
        try:
            entries = tuple(self._application_source())
        except Exception as exc:
            log.bind(action="discover_applications").warning(
                "Failed to discover desktop applications: %s",
                exc,
            )
            return False
        installed: dict[str, ApplicationInfo] = {}
        for entry in entries:
            try:
                info = self._info_from_listing_entry(entry)
            except Exception as exc:
                log.bind(action="build_app_info").warning(
                    "Failed to build ApplicationInfo: %s",
                    exc,
                )
                continue
            if info is None:
                continue
            installed.setdefault(info.desktop_id, info)
        ordered = tuple(
            sorted(
                installed.values(),
                key=lambda a: (_normalize_name(a.name), a.desktop_id.casefold()),
            )
        )
        alias_index: dict[str, list[str]] = {}
        executable_index: dict[Path, list[str]] = {}
        for info in ordered:
            for alias in info.aliases:
                ids = alias_index.setdefault(alias, [])
                if info.desktop_id not in ids:
                    ids.append(info.desktop_id)
            if info.executable_path is not None:
                ids = executable_index.setdefault(info.executable_path, [])
                if info.desktop_id not in ids:
                    ids.append(info.desktop_id)
        changed = (
            not self._loaded
            or installed != self._installed_by_id
            or ordered != self._ordered_snapshot
        )
        self._loaded = True
        if changed:
            self._installed_by_id = installed
            self._ordered_snapshot = ordered
            self._alias_index = alias_index
            self._executable_index = executable_index
            self._generation += 1
            self._notify_listeners()
        if self._started:
            self._sync_directory_monitors()
        return changed

    def resolve(
        self, desktop_id: str, *, log_failures: bool = True
    ) -> ApplicationInfo | None:
        info = self._installed_by_id.get(desktop_id)
        if info is not None:
            return info
        from docking.platform.launcher import Launcher

        _fallback = Launcher()
        resolved = _fallback.resolve(desktop_id, log_failures=log_failures)
        if resolved is not None:
            info = _build_info(
                desktop_id=resolved.desktop_id,
                name=resolved.name,
                icon_name=resolved.icon_name,
                wm_class=resolved.wm_class,
                exec_line=resolved.exec_line,
                desktop_file=desktop_entries.find_desktop_file(desktop_id),
            )
            self._installed_by_id[desktop_id] = info
            return info
        return None

    def resolve_all_by_wm_class(self, wm_class: str) -> tuple[ApplicationInfo, ...]:
        lookup = wm_class.lower().strip()
        if not lookup or lookup not in self._alias_index:
            return ()
        ids = self._alias_index[lookup]
        return tuple(
            info for did in ids if (info := self._installed_by_id.get(did)) is not None
        )

    def resolve_by_executable_path(
        self, executable_path: Path
    ) -> ApplicationInfo | None:
        try:
            lookup = executable_path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        ids = self._executable_index.get(lookup, [])
        for did in ids:
            info = self._installed_by_id.get(did)
            if info is not None:
                return info
        return None

    def list_applications(self) -> list[ApplicationInfo]:
        return list(self._ordered_snapshot)

    def snapshot(self) -> tuple[ApplicationInfo, ...]:
        return self._ordered_snapshot

    def get(self, desktop_id: str) -> ApplicationInfo | None:
        return self._installed_by_id.get(desktop_id)

    def add_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        with suppress(ValueError):
            self._listeners.remove(callback)

    def _info_from_listing_entry(self, entry: object) -> ApplicationInfo | None:
        desktop_id = _first_text(
            getattr(entry, "desktop_id", ""),
            _safe_call(entry, "get_id"),
        )
        if not desktop_id:
            return None
        app_info = getattr(entry, "app_info", None)
        name = _first_text(
            _safe_call(entry, "get_display_name"),
            getattr(entry, "name", ""),
            desktop_id.removesuffix(desktop_entries.DESKTOP_SUFFIX),
            desktop_id,
        )
        categories = getattr(entry, "categories", "") or ""
        if not categories:
            cat_val = _safe_call(entry, "get_categories")
            categories = str(cat_val) if cat_val else ""
        icon_name = getattr(entry, "icon_name", "") or ""
        if app_info is not None:
            wm_class = desktop_entries.wm_class_for_app_info(
                app_info=app_info,
                desktop_id=desktop_id,
            )
            exec_line = app_info.get_commandline() or ""
            description = _clean_text(app_info.get_description())
            generic_name = _clean_text(app_info.get_generic_name())
            keywords = _normalise_values(app_info.get_keywords())
            actions = _actions_from_gio(app_info)
            gicon = app_info.get_icon()
            if gicon is not None and not icon_name:
                icon_name = _clean_text(gicon.to_string())
        else:
            wm_class = ""
            exec_line = ""
            description = ""
            generic_name = ""
            keywords: tuple[str, ...] = ()
            actions: tuple[ApplicationAction, ...] = ()
        desktop_file = desktop_entries.find_desktop_file(desktop_id)
        if desktop_file is not None:
            key_file = desktop_entries.load_desktop_key_file(desktop_file)
            if key_file is not None:
                if not wm_class:
                    wm_class = desktop_entries.desktop_entry_string(
                        key_file, "StartupWMClass"
                    )
                if not exec_line:
                    exec_line = desktop_entries.desktop_entry_string(key_file, "Exec")
                if not description:
                    description = _first_text(
                        desktop_entries.desktop_entry_locale_string(
                            key_file, "Comment"
                        ),
                        desktop_entries.desktop_entry_locale_string(
                            key_file, "GenericName"
                        ),
                    )
                if not generic_name:
                    generic_name = desktop_entries.desktop_entry_locale_string(
                        key_file,
                        "GenericName",
                    )
                if not keywords:
                    keywords = _normalise_values(
                        desktop_entries.desktop_entry_locale_string(
                            key_file, "Keywords"
                        ),
                    )
                if not icon_name:
                    icon_name = desktop_entries.desktop_entry_string(key_file, "Icon")
                file_actions = desktop_entries.desktop_file_actions(desktop_file)
                actions = _merge_actions(
                    actions,
                    tuple(
                        ApplicationAction(a.action_id, a.display_name)
                        for a in file_actions
                    ),
                )
        if not wm_class:
            exec_basename = desktop_entries.normalized_exec_basename(exec_line)
            wm_class = exec_basename or desktop_id.removesuffix(
                desktop_entries.DESKTOP_SUFFIX
            )
        if not icon_name:
            icon_name = desktop_entries.FALLBACK_ICON
        return _build_info(
            desktop_id=desktop_id,
            name=name or desktop_id,
            icon_name=icon_name,
            wm_class=wm_class,
            exec_line=exec_line,
            categories=categories,
            description=description,
            generic_name=generic_name,
            keywords=keywords,
            desktop_file=desktop_file,
            actions=actions,
        )

    def _connect_app_monitor(self) -> None:
        try:
            monitor = self._app_monitor_factory()
            handler = monitor.connect("changed", self._on_catalog_changed)
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
            wanted = {Path(p).expanduser() for p in self._desktop_directories_source()}
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
                handler = monitor.connect("changed", self._on_catalog_changed)
            except Exception as exc:
                log.bind(action="monitor_desktop_directory", path=str(path)).warning(
                    "Could not monitor desktop application directory: %s",
                    exc,
                )
                continue
            self._directory_monitors[path] = (monitor, handler)

    def _cancel_directory_monitor(self, path: Path) -> None:
        entry = self._directory_monitors.pop(path, None)
        if entry is None:
            return
        monitor, handler = entry
        if handler is not None:
            with suppress(AttributeError, TypeError, ValueError, GLib.Error):
                monitor.disconnect(handler)
        with suppress(AttributeError, TypeError, ValueError, GLib.Error):
            monitor.cancel()

    def _cancel_directory_monitors(self) -> None:
        for path in tuple(self._directory_monitors):
            self._cancel_directory_monitor(path)

    def _on_catalog_changed(self, *_args: object) -> None:
        self._queue_refresh()

    def _queue_refresh(self) -> None:
        if not self._started or self._debounce_source_id is not None:
            return
        lifecycle_token = self._lifecycle_token

        def run_refresh() -> bool:
            self._debounce_source_id = None
            if not self._started or lifecycle_token != self._lifecycle_token:
                return False
            self.refresh()
            return False

        self._debounce_source_id = self._schedule_timeout(
            self._debounce_ms, run_refresh
        )

    def _cancel_pending_refresh(self) -> None:
        source_id = self._debounce_source_id
        self._debounce_source_id = None
        if source_id is None:
            return
        with suppress(TypeError, ValueError, GLib.Error):
            self._cancel_timeout(source_id)

    def _notify_listeners(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception as exc:
                log.bind(action="notify_registry_listener").warning(
                    "Application registry listener failed: %s",
                    exc,
                )


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.casefold().split())


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _first_text(*values: object) -> str:
    return next((text for v in values if (text := _clean_text(v))), "")


def _safe_call(target: object | None, method_name: str, *args: object) -> object:
    if target is None:
        return None
    method = getattr(target, method_name, None)
    if not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None


def _normalise_values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw: list[str] = value.split(";")
    elif isinstance(value, (list, tuple)):
        raw = [str(v) for v in value]
    else:
        raw = [str(value)]
    result: list[str] = []
    seen: set[str] = set()
    for v in raw:
        clean = _clean_text(v)
        key = _normalize_name(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return tuple(result)


def _actions_from_gio(app_info: object) -> tuple[ApplicationAction, ...]:
    action_ids = _safe_call(app_info, "list_actions")
    if not isinstance(action_ids, (list, tuple)):
        return ()
    if not action_ids:
        return ()
    actions: list[ApplicationAction] = []
    for action_id in action_ids:
        aid = _clean_text(action_id)
        if not aid:
            continue
        name = _clean_text(_safe_call(app_info, "get_action_name", aid))
        if name:
            actions.append(ApplicationAction(aid, name))
    return tuple(actions)


def _merge_actions(
    *groups: tuple[ApplicationAction, ...],
) -> tuple[ApplicationAction, ...]:
    merged: dict[str, ApplicationAction] = {}
    for actions in groups:
        for action in actions:
            merged.setdefault(action.action_id, action)
    return tuple(merged.values())
