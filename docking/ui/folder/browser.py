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

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import gi

from docking.platform.files import file_for_uri

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
from gi.repository import GdkPixbuf, Gio

from docking.i18n import _
from docking.log import get_logger
from docking.platform.targets import TargetService

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


@dataclass
class _FolderEntry:
    """Cached metadata with an icon loaded only when a caller needs it."""

    row: FolderRow
    gicon: Gio.Icon | None
    content_type: str
    modified_usec: int
    icon_loaded: bool = False


class FolderBrowser:
    """List folder children with bounded caching and stable sort behavior."""

    def __init__(
        self,
        *,
        target_service: TargetService,
    ) -> None:
        self._target_service = target_service
        self._directory_rows: OrderedDict[
            tuple[str, bool, int], tuple[int | None, list[_FolderEntry]]
        ] = OrderedDict()

    def invalidate_target(self, target: str, *, retain_icons: bool = False) -> None:
        uri = self._target_service.normalize_file_target(target)
        if uri is None:
            return
        for key in [key for key in self._directory_rows if key[0] == uri]:
            if retain_icons:
                _, entries = self._directory_rows[key]
                self._directory_rows[key] = (None, entries)
            else:
                self._directory_rows.pop(key)

    def target_state(self, target: str) -> str:
        uri = self._target_service.normalize_file_target(target)
        if uri is None:
            return "missing"
        try:
            folder = file_for_uri(uri)
            return "ok" if folder.query_exists(None) else "missing"
        except Exception as exc:
            log.debug("Failed to query folder target %s: %s", target, exc)
            return "missing"

    def cache_stamp(self, target: str) -> int:
        uri = self._target_service.normalize_file_target(target)
        if uri is None:
            return 0
        try:
            folder = file_for_uri(uri)
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
        icon_limit: int | None = None,
    ) -> list[FolderRow]:
        """Return all sorted rows, loading icons only for the requested prefix.

        Menus request the default full list. Stacks need nine icons, but retain
        the complete metadata listing for sorting and the hidden-row count.
        Hydrated entries stay cached so opening a menu or changing sort order
        neither loses existing icons nor repeatedly decodes failed thumbnails.
        """
        uri = self._target_service.normalize_file_target(target)
        if uri is None:
            return []
        resolved_icon_px = FOLDER_SMALL_ICON_PX if icon_px is None else max(icon_px, 1)
        cache_key = (uri, bool(prefs.show_hidden), resolved_icon_px)
        stamp = self.cache_stamp(target)
        cached = self._directory_rows.get(cache_key)
        if cached is None or cached[0] != stamp:
            found = self._read_directory(uri=uri, show_hidden=prefs.show_hidden)
            if found is None:
                return []
            if cached is not None:
                self._reuse_icons(previous=cached[1], current=found)
            cached = (stamp, found)
            self._directory_rows[cache_key] = cached
            while len(self._directory_rows) > FOLDER_DIRECTORY_CACHE_MAX_ENTRIES:
                self._directory_rows.popitem(last=False)
        self._directory_rows.move_to_end(cache_key)
        entries = sorted(
            cached[1], key=lambda entry: self.sort_key(row=entry.row, mode=prefs.sort)
        )
        limit = len(entries) if icon_limit is None else max(icon_limit, 0)
        for entry in entries[:limit]:
            if not entry.icon_loaded:
                row = entry.row
                entry.row = replace(
                    row,
                    icon=self._target_service.resolve_file_icon(
                        target=row.target,
                        gicon=entry.gicon,
                        content_type=entry.content_type,
                        size=resolved_icon_px,
                        is_dir=row.is_dir,
                    ),
                )
                entry.icon_loaded = True
        return [entry.row for entry in entries]

    @staticmethod
    def _reuse_icons(
        *, previous: list[_FolderEntry], current: list[_FolderEntry]
    ) -> None:
        hydrated = {entry.row.target: entry for entry in previous if entry.icon_loaded}
        for entry in current:
            old = hydrated.get(entry.row.target)
            if old is None:
                continue
            if (
                entry.row.is_dir == old.row.is_dir
                and entry.row.size == old.row.size
                and entry.row.modified == old.row.modified
                and entry.modified_usec == old.modified_usec
                and entry.content_type == old.content_type
                and (
                    entry.gicon is old.gicon
                    or (
                        entry.gicon is not None
                        and old.gicon is not None
                        and entry.gicon.equal(old.gicon)
                    )
                )
            ):
                entry.row = replace(entry.row, icon=old.row.icon)
                entry.icon_loaded = True

    def _read_directory(
        self, *, uri: str, show_hidden: bool
    ) -> list[_FolderEntry] | None:
        try:
            folder = file_for_uri(uri)
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
                        "time::modified-usec",
                    )
                ),
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except Exception as exc:
            log.warning("Failed to enumerate folder menu target %s: %s", uri, exc)
            return None

        entries: list[_FolderEntry] = []
        while True:
            info = enumerator.next_file(None)
            if info is None:
                break
            if info.get_is_hidden() and not show_hidden:
                continue
            child = folder.get_child(info.get_name())
            child_uri = child.get_uri()
            icon = info.get_icon()
            is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
            entries.append(
                _FolderEntry(
                    row=FolderRow(
                        target=child_uri,
                        name=info.get_display_name() or info.get_name(),
                        kind="dir" if is_dir else "file",
                        is_dir=is_dir,
                        has_children=(
                            self.directory_has_visible_children(
                                target=child_uri,
                                show_hidden=show_hidden,
                            )
                            if is_dir
                            else False
                        ),
                        size=int(info.get_size()),
                        created=int(info.get_attribute_uint64("time::created")),
                        modified=int(info.get_attribute_uint64("time::modified")),
                        icon=None,
                    ),
                    gicon=icon,
                    content_type=info.get_content_type() or "",
                    modified_usec=info.get_attribute_uint32("time::modified-usec"),
                )
            )
        return entries

    def directory_has_visible_children(self, target: str, show_hidden: bool) -> bool:
        uri = self._target_service.normalize_file_target(target)
        if uri is None:
            return False
        try:
            folder = file_for_uri(uri)
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
        row: FolderRow,
        mode: str,
    ) -> tuple[str | int, ...]:
        name = row.name.casefold()
        if mode == "kind":
            return (row.kind, name)
        if mode == "size":
            return (row.size, name)
        if mode == "created":
            return (row.created, name)
        if mode == "modified":
            return (row.modified, name)
        return (name,)
