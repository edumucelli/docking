"""Desktop application discovery and source-merging adapters."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

from docking.log import get_logger, with_context

from . import entries as desktop_entries
from .types import (
    ActionSource,
    ApplicationAction,
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
    TransientApplicationInfo,
)

log = with_context(get_logger(name="application_discovery"))


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    applications: tuple[ApplicationInfo, ...]
    handles: dict[str, object]
    transient: tuple[TransientApplicationInfo, ...]
    transient_handles: dict[str, object]
    presentation_order: tuple[str, ...]


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


def default_gio_applications() -> tuple[object, ...]:
    """Return only desktop-backed entries from Gio's broad app-info list."""
    return tuple(
        app_info
        for app_info in Gio.AppInfo.get_all()
        if is_gio_desktop_app_info(app_info)
    )


def is_gio_desktop_app_info(app_info: object) -> bool:
    return isinstance(app_info, Gio.DesktopAppInfo)


def discover(
    *,
    application_source: Callable[[], Iterable[object]],
    desktop_directories_source: Callable[[], Iterable[Path]],
    desktop_app_info_for_id: Callable[[str], object | None],
    desktop_app_info_from_filename: Callable[[str], object | None],
    handle_epoch: int,
) -> DiscoveryResult:
    gio_entries = tuple(application_source())
    directories = unique_paths(desktop_directories_source())
    file_winners, file_order = _discover_desktop_files(directories)

    gio_by_id: dict[str, object] = {}
    gio_order: list[str] = []
    transient: list[TransientApplicationInfo] = []
    transient_handles: dict[str, object] = {}
    for source_position, app_info in enumerate(gio_entries):
        application_id = desktop_id(app_info)
        if not application_id:
            listing = transient_from_gio(
                app_info=app_info,
                listing_key=f"gio-idless:{handle_epoch}:{source_position}",
                require_visible=True,
            )
            if listing is not None:
                transient.append(listing)
                transient_handles[listing.listing_key] = app_info
            continue
        if application_id in gio_by_id:
            continue
        gio_by_id[application_id] = app_info
        gio_order.append(application_id)

    applications: list[ApplicationInfo] = []
    handles: dict[str, object] = {}
    consumed_gio_ids: set[str] = set()

    for application_id in file_order:
        path = file_winners[application_id]
        facts = file_facts(path)
        if facts is not None and (not facts.is_application or facts.hidden):
            consumed_gio_ids.add(application_id)
            continue

        app_info = gio_by_id.get(application_id)
        if app_info is None:
            app_info = _app_info_for_id(application_id, desktop_app_info_for_id)
            if app_info is None:
                app_info = _app_info_from_filename(
                    path,
                    desktop_app_info_from_filename,
                )
        else:
            consumed_gio_ids.add(application_id)

        if app_info is None:
            if facts is None or not facts.is_application:
                continue
            application = _application_from_file(
                desktop_id=application_id,
                path=path,
                facts=facts,
            )
        else:
            application = application_from_gio(
                desktop_id=application_id,
                app_info=app_info,
                fallback_path=path,
                fallback_facts=facts,
            )
            if application is not None:
                handles[application_id] = app_info
        if application is not None:
            applications.append(application)

    for application_id in gio_order:
        if application_id in consumed_gio_ids or application_id in file_winners:
            continue
        app_info = gio_by_id[application_id]
        application = application_from_gio(
            desktop_id=application_id,
            app_info=app_info,
            fallback_path=None,
            fallback_facts=None,
        )
        if application is None:
            continue
        applications.append(application)
        handles[application_id] = app_info

    return DiscoveryResult(
        applications=tuple(applications),
        handles=handles,
        transient=tuple(transient),
        transient_handles=transient_handles,
        presentation_order=(
            *gio_order,
            *(
                application_id
                for application_id in file_order
                if application_id not in gio_by_id
            ),
        ),
    )


def transient_from_gio(
    *,
    app_info: object,
    listing_key: str,
    require_visible: bool,
) -> TransientApplicationInfo | None:
    if require_visible and (
        _safe_bool_call(app_info, "get_is_hidden")
        or _safe_bool_call(app_info, "get_nodisplay")
    ):
        return None
    icon = safe_call(app_info, "get_icon")
    filename = source_text(safe_call(app_info, "get_filename"))
    application_id = desktop_id(app_info)
    categories_raw = source_text(safe_call(app_info, "get_categories"))
    return TransientApplicationInfo(
        listing_key=listing_key,
        name=(
            source_text(safe_call(app_info, "get_display_name"))
            or application_id
            or "Unknown"
        ),
        categories=_normalise_values(categories_raw),
        categories_raw=categories_raw,
        declared_icon=source_text(safe_call(icon, "to_string")),
        desktop_file=Path(filename).expanduser() if filename else None,
        exec_line=source_text(safe_call(app_info, "get_commandline")),
        description=source_text(safe_call(app_info, "get_description")),
        generic_name=source_text(safe_call(app_info, "get_generic_name")),
    )


def desktop_file_keys(path: Path) -> tuple[Path, ...]:
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


def desktop_id(app_info: object) -> str:
    return _plain_text(
        safe_call(app_info, "get_id") or getattr(app_info, "desktop_id", "")
    )


def desktop_filename(app_info: object) -> str:
    return source_text(safe_call(app_info, "get_filename"))


def file_facts(path: Path | None) -> _FileFacts | None:
    if path is None:
        return None
    key_file = desktop_entries.load_desktop_key_file(path)
    if key_file is None:
        return None

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
        action_values.append(_FileAction(action_id, name, exec_line))

    return _FileFacts(
        is_application=(
            desktop_entries.desktop_entry_string(key_file, "Type") == "Application"
        ),
        hidden=desktop_entries.desktop_entry_bool(key_file, "Hidden"),
        no_display=desktop_entries.desktop_entry_bool(key_file, "NoDisplay"),
        generated=desktop_entries.desktop_entry_bool(
            key_file,
            desktop_entries.GENERATED_MARKER_KEY,
        ),
        name=desktop_entries.desktop_entry_locale_string(key_file, "Name"),
        declared_icon=desktop_entries.desktop_entry_string(key_file, "Icon"),
        startup_wm_class=desktop_entries.desktop_entry_string(
            key_file,
            "StartupWMClass",
        ),
        exec_line=desktop_entries.desktop_entry_string(key_file, "Exec"),
        generic_name=desktop_entries.desktop_entry_locale_string(
            key_file,
            "GenericName",
        ),
        description=desktop_entries.desktop_entry_locale_string(key_file, "Comment"),
        categories_raw=desktop_entries.desktop_entry_string(key_file, "Categories"),
        keywords=_normalise_values(
            desktop_entries.desktop_entry_locale_string(key_file, "Keywords")
        ),
        actions=tuple(action_values),
    )


def application_from_gio(
    *,
    desktop_id: str,
    app_info: object,
    fallback_path: Path | None,
    fallback_facts: _FileFacts | None,
) -> ApplicationInfo | None:
    if _safe_bool_call(app_info, "get_is_hidden"):
        return None

    filename = desktop_filename(app_info)
    desktop_file = Path(filename).expanduser() if filename else fallback_path
    visibility_facts = fallback_facts
    facts = fallback_facts
    if desktop_file is not None and desktop_file != fallback_path:
        facts = file_facts(desktop_file)
    if facts is not None and (not facts.is_application or facts.hidden):
        return None

    exec_line = source_text(safe_call(app_info, "get_commandline"))
    startup_wm_class = source_text(safe_call(app_info, "get_startup_wm_class"))
    icon = safe_call(app_info, "get_icon")
    declared_icon = source_text(safe_call(icon, "to_string"))
    name = source_text(safe_call(app_info, "get_display_name")) or desktop_id
    generic_name = source_text(safe_call(app_info, "get_generic_name"))
    if not _clean_text(generic_name) and facts is not None:
        generic_name = facts.generic_name

    description = source_text(safe_call(app_info, "get_description"))
    if not _clean_text(description) and facts is not None:
        description = facts.description or facts.generic_name

    raw_categories = safe_call(app_info, "get_categories")
    categories_raw = source_text(raw_categories) if raw_categories is not None else ""
    keywords = _normalise_values(safe_call(app_info, "get_keywords"))
    if not keywords and facts is not None:
        keywords = facts.keywords

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
            generated=bool(facts and facts.generated),
        ),
        location=_location(desktop_file),
        desktop_file=desktop_file,
        visible=(
            not _safe_bool_call(app_info, "get_nodisplay")
            and (visibility_facts is None or not visibility_facts.no_display)
            and (facts is None or not facts.no_display)
        ),
        has_gio_source=True,
        generic_name=generic_name,
        description=description,
        categories_raw=categories_raw,
        keywords=keywords,
        actions=_merge_actions(
            _gio_actions(app_info),
            facts.actions if facts is not None else (),
        ),
    )


def safe_call(
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


def source_text(value: object) -> str:
    return "" if value is None else str(value)


def _app_info_from_filename(
    path: Path,
    loader: Callable[[str], object | None],
) -> object | None:
    try:
        return loader(str(path))
    except Exception as exc:
        log.bind(action="load_desktop_app_info", path=str(path)).debug(
            "Gio could not load desktop file: %s",
            exc,
        )
        return None


def _app_info_for_id(
    desktop_id: str,
    loader: Callable[[str], object | None],
) -> object | None:
    try:
        return loader(desktop_id)
    except Exception as exc:
        log.bind(action="load_desktop_app_info", desktop_id=desktop_id).debug(
            "Gio could not load desktop ID: %s",
            exc,
        )
        return None


def unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for value in paths:
        path = Path(value).expanduser()
        if path not in seen:
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
            application_id = path.relative_to(directory).as_posix()
            if application_id not in winners:
                winners[application_id] = path
                order.append(application_id)
    return winners, order


def _application_from_file(
    *,
    desktop_id: str,
    path: Path,
    facts: _FileFacts,
) -> ApplicationInfo:
    return _make_application(
        desktop_id=desktop_id,
        name=facts.name or desktop_id,
        declared_icon=facts.declared_icon,
        wm_class=_wm_class(
            desktop_id=desktop_id,
            startup_wm_class=facts.startup_wm_class,
            exec_line=facts.exec_line,
        ),
        exec_line=facts.exec_line,
        origin=_origin(desktop_id=desktop_id, generated=facts.generated),
        location=_location(path),
        desktop_file=path,
        visible=not facts.no_display,
        has_gio_source=False,
        generic_name=facts.generic_name,
        description=(
            facts.description if _clean_text(facts.description) else facts.generic_name
        ),
        categories_raw=facts.categories_raw,
        keywords=facts.keywords,
        actions=_merge_actions((), facts.actions),
    )


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
        aliases=tuple(desktop_entries.match_aliases(desktop_id, wm_class, exec_line)),
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
    result: list[tuple[str, str]] = []
    for raw_action_id in safe_call(app_info, "list_actions") or ():
        action_id = _clean_text(raw_action_id)
        name = _clean_text(safe_call(app_info, "get_action_name", action_id))
        if action_id and name:
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
        if action_id and name and action_id not in merged:
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
        elif ActionSource.DESKTOP_FILE not in existing.sources:
            merged[action_id] = ApplicationAction(
                action_id=existing.action_id,
                name=existing.name,
                sources=existing.sources | {ActionSource.DESKTOP_FILE},
                file_exec_line=file_action.exec_line,
            )
    return tuple(merged.values())


def _wm_class(*, desktop_id: str, startup_wm_class: str, exec_line: str) -> str:
    wine_aliases = desktop_entries.wine_executable_aliases(exec_line)
    if startup_wm_class and startup_wm_class.lower() != "wine":
        return startup_wm_class
    if wine_aliases:
        return wine_aliases[0]
    return desktop_entries.normalized_exec_basename(
        exec_line
    ) or desktop_id.removesuffix(desktop_entries.DESKTOP_SUFFIX)


def _gio_wm_class(*, desktop_id: str, startup_wm_class: str, exec_line: str) -> str:
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


def _origin(*, desktop_id: str, generated: bool) -> ApplicationOrigin:
    if generated or desktop_id.startswith(desktop_entries.GENERATED_DESKTOP_PREFIX):
        return ApplicationOrigin.GENERATED
    return ApplicationOrigin.INSTALLED


def _location(path: Path | None) -> ApplicationLocation:
    return (
        ApplicationLocation.HOST
        if desktop_entries.is_host_desktop_file(path)
        else ApplicationLocation.SANDBOX
    )


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
        key = " ".join(unicodedata.normalize("NFKC", clean).casefold().split())
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return tuple(result)


def _plain_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _clean_text(value: object) -> str:
    return "" if value is None else " ".join(str(value).split())


def _safe_bool_call(target: object, method_name: str) -> bool:
    return bool(safe_call(target, method_name))
