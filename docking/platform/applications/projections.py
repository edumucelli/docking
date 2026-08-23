"""Explicit consumer views over canonical application metadata."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .constants import FALLBACK_ICON
from .types import (
    ActionSource,
    ApplicationAction,
    ApplicationInfo,
    ApplicationOrigin,
)

NEW_WINDOW_ACTION_ID = "new-window"


class DockMetadata(NamedTuple):
    """Presentation fields copied from an application into a dock item."""

    desktop_id: str
    name: str
    icon_name: str
    wm_class: str
    exec_line: str


@dataclass(frozen=True, slots=True)
class VisibleApplication:
    """Toolkit-independent metadata for an application-listing row."""

    desktop_id: str
    name: str
    categories: str
    icon_name: str
    desktop_file: Path | None


@dataclass(frozen=True, slots=True)
class IconDescriptor:
    """A toolkit-independent search icon reference."""

    kind: str
    value: str


@dataclass(frozen=True, slots=True)
class DesktopActionProjection:
    """One named action exposed to a presentation."""

    action_id: str
    name: str


@dataclass(frozen=True, slots=True)
class SearchApplication:
    """Plain application metadata consumed by search."""

    desktop_id: str
    name: str
    normalized_name: str
    categories: tuple[str, ...]
    icon: IconDescriptor
    description: str = ""
    keywords: tuple[str, ...] = ()
    actions: tuple[DesktopActionProjection, ...] = ()


def normalize_search_text(value: str) -> str:
    """Return stable Unicode- and case-normalized search text."""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.casefold().split())


def dock_icon_name(info: ApplicationInfo) -> str:
    """Return the declared icon or the dock's generic fallback."""
    if (
        info.origin is ApplicationOrigin.GENERATED
        and info.declared_icon in {"", FALLBACK_ICON}
        and info.executable_path is not None
    ):
        executable = info.executable_path
        stem = (
            executable.name[: -len(".AppImage")]
            if executable.name.lower().endswith(".appimage")
            else executable.stem
            if executable.suffix
            else executable.name
        )
        for suffix in (".svg", ".png", ".xpm"):
            candidate = executable.with_name(f"{stem}{suffix}")
            if candidate.is_file():
                try:
                    return str(candidate.resolve(strict=True))
                except (OSError, RuntimeError):
                    continue
        if executable.name.lower().endswith(".appimage"):
            return "application-x-appimage"
    return info.declared_icon or FALLBACK_ICON


def dock_metadata(info: ApplicationInfo) -> DockMetadata:
    """Project canonical metadata into dock presentation fields."""
    return DockMetadata(
        desktop_id=info.desktop_id,
        name=info.name,
        icon_name=dock_icon_name(info),
        wm_class=info.wm_class,
        exec_line=info.exec_line,
    )


def visible_listing(info: ApplicationInfo) -> VisibleApplication | None:
    """Return a listing row only when the application is menu-visible."""
    if not info.visible:
        return None
    return VisibleApplication(
        desktop_id=info.desktop_id,
        name=info.name,
        categories=info.categories_raw,
        icon_name=info.declared_icon,
        desktop_file=info.desktop_file,
    )


def visible_listings(
    applications: Iterable[ApplicationInfo],
) -> tuple[VisibleApplication, ...]:
    """Project an iterable while preserving its presentation order."""
    projected: list[VisibleApplication] = []
    for info in applications:
        listing = visible_listing(info)
        if listing is not None:
            projected.append(listing)
    return tuple(projected)


def search_icon(info: ApplicationInfo) -> IconDescriptor:
    """Classify the declared icon without adding the dock fallback."""
    value = _clean_text(info.declared_icon)
    if not value:
        return IconDescriptor(kind="none", value="")
    if value.startswith("file:") or Path(value).is_absolute():
        return IconDescriptor(kind="file", value=value)
    if "://" in value:
        return IconDescriptor(kind="serialized", value=value)
    return IconDescriptor(kind="themed", value=value)


def search_actions(
    info: ApplicationInfo,
) -> tuple[DesktopActionProjection, ...]:
    """Project Gio-first, file-only-second canonical actions for search."""
    return _project_actions(info.actions)


def quicklist_actions(
    info: ApplicationInfo,
) -> tuple[DesktopActionProjection, ...]:
    """Preserve the dock's source-exclusive quicklist behavior."""
    source = ActionSource.GIO if info.has_gio_source else ActionSource.DESKTOP_FILE
    return _project_actions(
        action for action in info.actions if source in action.sources
    )


def new_window_action(info: ApplicationInfo) -> ApplicationAction | None:
    """Return the Gio-routable new-window action, when present."""
    if not info.has_gio_source:
        return None
    for action in info.actions:
        if (
            action.action_id == NEW_WINDOW_ACTION_ID
            and ActionSource.GIO in action.sources
        ):
            return action
    return None


def search_metadata(info: ApplicationInfo) -> SearchApplication:
    """Project canonical metadata onto the current search value shape."""
    name = (
        _clean_text(info.name)
        or info.desktop_id.removesuffix(".desktop")
        or info.desktop_id
    )
    return SearchApplication(
        desktop_id=info.desktop_id,
        name=name,
        normalized_name=normalize_search_text(name),
        categories=_normalise_values(info.categories),
        icon=search_icon(info),
        description=_clean_text(info.description),
        keywords=_normalise_values(info.keywords),
        actions=search_actions(info),
    )


def search_applications(
    applications: Iterable[ApplicationInfo],
) -> tuple[SearchApplication, ...]:
    """Project visible records and apply the current search ordering."""
    projected = [search_metadata(info) for info in applications if info.visible]
    return tuple(
        sorted(
            projected,
            key=lambda application: (
                application.normalized_name,
                application.desktop_id.casefold(),
            ),
        )
    )


def _project_actions(
    actions: Iterable[ApplicationAction],
) -> tuple[DesktopActionProjection, ...]:
    projected: list[DesktopActionProjection] = []
    seen: set[str] = set()
    for action in actions:
        action_id = _clean_text(action.action_id)
        name = _clean_text(action.name)
        if not action_id or not name or action_id in seen:
            continue
        seen.add(action_id)
        projected.append(DesktopActionProjection(action_id=action_id, name=name))
    return tuple(projected)


def _normalise_values(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _clean_text(value)
        key = normalize_search_text(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return tuple(result)


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


__all__ = [
    "FALLBACK_ICON",
    "NEW_WINDOW_ACTION_ID",
    "DesktopActionProjection",
    "DockMetadata",
    "IconDescriptor",
    "SearchApplication",
    "VisibleApplication",
    "dock_icon_name",
    "dock_metadata",
    "new_window_action",
    "normalize_search_text",
    "quicklist_actions",
    "search_actions",
    "search_applications",
    "search_icon",
    "search_metadata",
    "visible_listing",
    "visible_listings",
]
