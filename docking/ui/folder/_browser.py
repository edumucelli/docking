# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Private directory browser owned by the folder stack controller."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
from gi.repository import GdkPixbuf, Gio

import docking.platform.launcher as launcher_mod
from docking.i18n import _
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.platform.launcher import Launcher


FOLDER_DIRECTORY_CACHE_MAX_ENTRIES = 48
FOLDER_SMALL_ICON_PX = 16
FOLDER_SORT_OPTIONS = (
    (_("Name"), "name"),
    (_("Kind"), "kind"),
    (_("Size"), "size"),
    (_("Created"), "created"),
    (_("Modified"), "modified"),
)

log = get_logger("folder.browser")


@dataclass(frozen=True)
class FolderPrefs:
    """Persistent folder display preferences."""

    sort: str = "name"
    show_hidden: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> FolderPrefs:
        return cls(
            sort=str(raw.get("sort", "name") or "name"),
            show_hidden=bool(raw.get("show_hidden", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sort": self.sort,
            "show_hidden": self.show_hidden,
        }


@dataclass(frozen=True)
class FolderRow:
    """One visible child row in a browsed folder."""

    target: str
    name: str
    kind: str
    is_dir: bool
    has_children: bool
    size: int
    created: int
    modified: int
    icon: GdkPixbuf.Pixbuf | None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "name": self.name,
            "kind": self.kind,
            "is_dir": self.is_dir,
            "has_children": self.has_children,
            "size": self.size,
            "created": self.created,
            "modified": self.modified,
            "icon": self.icon,
        }


class FolderBrowser:
    """List folder children with bounded caching and stable sort behavior."""

    def __init__(self, launcher: Launcher | None = None) -> None:
        self._launcher = launcher
        self._directory_rows: dict[tuple[str, int, bool, int], list[FolderRow]] = {}

    @staticmethod
    def _get_lru(cache: dict[Any, Any], key: Any) -> Any | None:
        cached = cache.pop(key, None)
        if cached is not None:
            cache[key] = cached
        return cached

    @staticmethod
    def _put_lru(
        cache: dict[Any, Any], key: Any, value: Any, *, max_entries: int
    ) -> None:
        cache[key] = value
        while len(cache) > max_entries:
            cache.pop(next(iter(cache)))

    def invalidate_target(self, target: str) -> None:
        uri = launcher_mod.normalize_file_target(target)
        if uri is None:
            return
        for key in [key for key in self._directory_rows if key[0] == uri]:
            self._directory_rows.pop(key, None)

    def target_state(self, target: str) -> str:
        uri = launcher_mod.normalize_file_target(target)
        if uri is None:
            return "missing"
        try:
            folder = Gio.File.new_for_uri(uri)
            return "ok" if folder.query_exists(None) else "missing"
        except Exception as exc:
            log.debug("Failed to query folder target %s: %s", target, exc)
            return "missing"

    def cache_stamp(self, target: str) -> int:
        uri = launcher_mod.normalize_file_target(target)
        if uri is None:
            return 0
        try:
            folder = Gio.File.new_for_uri(uri)
            info = folder.query_info(
                "time::modified",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except Exception:
            return 0
        return int(info.get_attribute_uint64("time::modified"))

    def list_directory(
        self,
        *,
        target: str,
        prefs: FolderPrefs,
        icon_px: int | None = None,
    ) -> list[FolderRow]:
        uri = launcher_mod.normalize_file_target(target)
        if uri is None:
            return []
        resolved_icon_px = FOLDER_SMALL_ICON_PX if icon_px is None else max(icon_px, 1)
        cache_key = (
            uri,
            self.cache_stamp(target),
            bool(prefs.show_hidden),
            resolved_icon_px,
        )
        cached = self._get_lru(self._directory_rows, cache_key)
        if cached is not None:
            rows = list(cached)
            rows.sort(key=lambda row: self.sort_key(row=row, mode=prefs.sort))
            return rows
        try:
            folder = Gio.File.new_for_uri(uri)
            enumerator = folder.enumerate_children(
                ",".join(
                    (
                        "standard::name",
                        "standard::display-name",
                        "standard::icon",
                        "standard::type",
                        "standard::content-type",
                        "standard::is-hidden",
                        "standard::size",
                        "time::created",
                        "time::modified",
                    )
                ),
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except Exception as exc:
            log.warning("Failed to enumerate folder menu target %s: %s", target, exc)
            return []

        rows: list[FolderRow] = []
        while True:
            info = enumerator.next_file(None)
            if info is None:
                break
            if info.get_is_hidden() and not prefs.show_hidden:
                continue
            child = folder.get_child(info.get_name())
            child_uri = child.get_uri()
            icon = info.get_icon()
            is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
            rows.append(
                FolderRow(
                    target=child_uri,
                    name=info.get_display_name() or info.get_name(),
                    kind="dir" if is_dir else "file",
                    is_dir=is_dir,
                    has_children=(
                        self.directory_has_visible_children(
                            target=child_uri,
                            show_hidden=prefs.show_hidden,
                        )
                        if is_dir
                        else False
                    ),
                    size=int(info.get_size()),
                    created=int(info.get_attribute_uint64("time::created")),
                    modified=int(info.get_attribute_uint64("time::modified")),
                    icon=(
                        self._launcher.resolve_file_icon(
                            target=child_uri,
                            gicon=icon,
                            content_type=info.get_content_type() or "",
                            size=resolved_icon_px,
                            is_dir=is_dir,
                        )
                    )
                    if self._launcher
                    else None,
                )
            )
        self._put_lru(
            self._directory_rows,
            cache_key,
            list(rows),
            max_entries=FOLDER_DIRECTORY_CACHE_MAX_ENTRIES,
        )
        rows.sort(key=lambda row: self.sort_key(row=row, mode=prefs.sort))
        return rows

    def directory_has_visible_children(self, target: str, show_hidden: bool) -> bool:
        uri = launcher_mod.normalize_file_target(target)
        if uri is None:
            return False
        try:
            folder = Gio.File.new_for_uri(uri)
            enumerator = folder.enumerate_children(
                "standard::is-hidden",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except Exception as exc:
            log.warning(
                "Failed to inspect folder children for target %s: %s",
                target,
                exc,
            )
            return False

        while True:
            info = enumerator.next_file(None)
            if info is None:
                return False
            if show_hidden or not info.get_is_hidden():
                return True

    def sort_key(
        self,
        row: FolderRow | Mapping[str, Any],
        mode: str,
    ) -> tuple[Any, ...]:
        value = row.as_dict() if isinstance(row, FolderRow) else row
        name = str(value["name"]).casefold()
        if mode == "kind":
            return (value["kind"], name)
        if mode == "size":
            return (value["size"], name)
        if mode == "created":
            return (value["created"], name)
        if mode == "modified":
            return (value["modified"], name)
        return (name,)
