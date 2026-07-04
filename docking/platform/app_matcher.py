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

"""Shared window-identity to desktop-ID matching for all display-server backends.

Every backend (X11/Wnck, Wayland/wlr-foreign-toplevel, Hyprland IPC,
Wayfire IPC, Niri IPC, GNOME Shell Bridge, KWin/AT-SPI) needs to answer
the same question:

    "Which Docking desktop ID does this running window belong to?"

The matcher handles Wine .exe disambiguation, space-to-hyphen-to-joined
candidate synthesis, GNOME prefix expansion, dot-suffix splitting, Snap
underscore-prefix handling, and missed-candidate memoization. All seven
backends (X11/Wnck, wlr-foreign-toplevel, Hyprland IPC, Wayfire IPC, Niri IPC,
GNOME Shell Bridge, KWin/AT-SPI) share the same matching engine, so a fix or
improvement applies everywhere at once.

``AppIdMatcher`` is a backend-agnostic matching engine. Backends extract
identity strings from their display server and pass them here as plain
strings. The matcher owns the heuristics; backends own only the display-server
extraction.

``sync_visible_items(items)`` rebuilds the fast-path alias cache from the
current dock model at the start of every running-window scan.

``cache_missed_desktop_ids`` is a constructor flag used by X11 so signal
bursts from Wnck do not hammer Gio with repeated failing lookups.
Wayland-style backends leave it disabled so newly installed or generated
desktop files can be matched without a Docking restart.

``match(app_id, *, instance_hint=None, prefer_raw_app_id=True,
defer_wm_class_lookup=False)`` maps a runtime window identity to a Docking
desktop ID:

    ``app_id`` is the primary identity from the display server:
    X11 uses ``class_group`` (WM_CLASS class part); Wayland compositors
    (Hyprland, Wayfire, Niri, GNOME) provide a single ``app_id`` or
    ``class`` string; AT-SPI uses ``app_name`` from the accessibility tree.

    ``instance_hint`` is an optional secondary identity. Only X11 provides
    this (WM_CLASS instance part). It enables Wine disambiguation: when
    ``app_id == "wine"`` the matcher extracts the ``.exe`` name from
    ``instance_hint`` instead.

    ``prefer_raw_app_id`` controls candidate order. Wayland-style backends
    keep compositor-provided app IDs first; X11 passes ``False`` to preserve
    the historical WM_CLASS lowercase-first order.

    ``defer_wm_class_lookup`` controls when the install-wide WM_CLASS index
    is consulted. X11 passes ``True`` to try all generated desktop IDs before
    the reverse alias lookup. Wayland-style backends keep it ``False`` so
    each increasingly broad candidate can check its exact alias before falling
    back to the next broader direct desktop ID.

Matching is strongest-first:

    1. Wine detection — when ``app_id`` is the generic ``"wine"`` class
       group and ``instance_hint`` contains a ``.exe`` path, extract the
       executable name and match against visible aliases and the launcher
       WM_CLASS index.

    2. Visible-item alias cache — fast-path lookup against currently pinned
       and transient dock items (no Gio or filesystem calls).

    3. Instance-hint match — when ``app_id`` is generic but
       ``instance_hint`` is specific and matches a visible item.

    4. Candidate generation and resolution — for each generated candidate
       string, in order:

       a. Check visible aliases (normalized).
       b. Try ``launcher.resolve(f"{candidate}.desktop")``.
       c. Try ``launcher.resolve_by_wm_class(candidate)``.

       Failed desktop IDs can be memoized for X11 so signal bursts do not
       hammer Gio. Successful WM_CLASS matches use the launcher's indexed
       lookup.

Candidates are generated in a stable, deterministic order:

    * Raw ``app_id``
    * Raw ``app_id`` without ``.desktop``
    * Lowercase variants of the above
    * Space to hyphen (``"mongodb compass"`` to ``"mongodb-compass"``)
    * Space to joined (``"mongodb compass"`` to ``"mongodbcompass"``)
    * GNOME prefix + original class-group (``"Files"`` to ``"org.gnome.Files"``)
    * Dot-suffix split (``"org.gnome.Nautilus"`` to ``"Nautilus"``)
    * Snap/container ``_`` prefix expansion (``"firefox_firefox"`` to
      ``"firefox"``)

Duplicates are removed while preserving first-occurrence order so the first
successful match is always the same for a given input.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from docking.platform.launcher import DESKTOP_SUFFIX, GNOME_APP_PREFIX

if TYPE_CHECKING:
    from docking.core.items import DockItem
    from docking.platform.launcher import Launcher


def _normalize_alias(value: str) -> str:
    """Normalize an alias for cache-key comparison (lowercase, no .desktop suffix)."""
    return value.strip().lower().removesuffix(DESKTOP_SUFFIX)


def _ensure_desktop_suffix(value: str) -> str:
    """Append ``.desktop`` to *value* when it is not already suffixed."""
    stripped = value.strip()
    return (
        stripped
        if stripped.lower().endswith(DESKTOP_SUFFIX)
        else f"{stripped}{DESKTOP_SUFFIX}"
    )


def _app_id_candidates(app_id: str) -> list[str]:
    """Generate lookup candidates from a Wayland-style single ``app_id``.

    Handles dot-separated prefixes (``org.gnome.Nautilus`` → ``Nautilus``)
    and Snap/container underscore notation
    (``firefox_firefox`` → ``firefox``).
    """
    stripped = app_id.strip()
    if not stripped:
        return []
    candidates = [
        stripped,
        stripped.removesuffix(DESKTOP_SUFFIX),
        stripped.lower(),
        stripped.lower().removesuffix(DESKTOP_SUFFIX),
    ]
    # Wine / Windows executable reported as the sole app_id by a Wayland
    # compositor (e.g. Hyprland `class` = "notepad.exe"). Strip the .exe
    # suffix so the launcher can match "notepad" → "wine-notepad.desktop".
    is_windows_executable = stripped.lower().endswith(".exe")
    if is_windows_executable:
        candidates.append(stripped[:-4])  # preserve original case
        candidates.append(stripped[:-4].lower())
    elif "." in stripped:
        candidates.append(stripped.split(".")[-1])
    # Snap / container app-ids like firefox_firefox.desktop: also try the
    # leading segment so the launcher can match firefox.desktop.
    body = stripped.removesuffix(DESKTOP_SUFFIX)
    if "_" in body:
        segments = body.split("_")
        prefixes = ["_".join(segments[: i + 1]) for i in range(len(segments) - 1)]
        for prefix in prefixes:
            candidates.append(prefix)
            candidates.append(f"{prefix}{DESKTOP_SUFFIX}")
            candidates.append(prefix.lower())
            candidates.append(f"{prefix.lower()}{DESKTOP_SUFFIX}")
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _class_group_candidates(*, class_lower: str, class_group: str) -> list[str]:
    """Generate desktop ID candidates from an X11-style WM_CLASS class group.

    Handles space→hyphen→joined transformations and GNOME prefix synthesis.
    """
    candidates = [class_lower]
    if " " in class_lower:
        candidates.append(class_lower.replace(" ", "-"))
        candidates.append(class_lower.replace(" ", ""))
    candidates.append(f"{GNOME_APP_PREFIX}{class_group}")
    return list(dict.fromkeys(candidates))


def _wine_aliases_from_instance(instance: str) -> list[str]:
    """Extract lookup aliases from a Wine ``class_instance`` path.

    For ``C:\\\\Program Files\\\\App\\\\Tool.exe`` this returns
    ``["tool.exe", "tool"]``. The full raw instance is also included
    so direct cache hits on the unfiltered string still work.
    """
    instance_lower = instance.lower().strip()
    basename = re.split(r"[\\/]", instance_lower)[-1]
    aliases = [basename]
    if basename.endswith(".exe"):
        aliases.append(basename[:-4])
    if instance_lower != basename:
        aliases.append(instance_lower)
    return list(dict.fromkeys(aliases))


class AppIdMatcher:
    """Shared window → desktop ID matching for all display-server backends.

    Backends extract identity from their display server (Wnck, Wayland
    protocol, compositor IPC, AT-SPI) and pass it here as plain strings.
    The matcher owns the heuristics for mapping runtime identities to
    Docking desktop IDs.
    """

    def __init__(
        self,
        launcher: Launcher,
        *,
        cache_missed_desktop_ids: bool = False,
    ) -> None:
        self._launcher = launcher
        self._cache_missed_desktop_ids = cache_missed_desktop_ids
        self._visible_aliases: dict[str, str] = {}  # normalized → desktop_id
        self._missed_candidates: set[str] = set()

    def sync_visible_items(self, items: Iterable[DockItem]) -> None:
        """Rebuild visible-item alias cache from the current dock model.

        Called at the start of every running-window scan so the cache
        reflects the current pinned / transient item set. Pinned items
        can be reordered, pinned, unpinned, or replaced at runtime, and
        using stale aliases would make running indicators disappear
        until restart.
        """
        self._visible_aliases.clear()
        for item in items:
            aliases = {
                item.desktop_id,
                item.desktop_id.removesuffix(DESKTOP_SUFFIX),
                getattr(item, "wm_class", "") or "",
            }
            for alias in aliases:
                normalized = _normalize_alias(alias)
                if normalized:
                    self._visible_aliases[normalized] = item.desktop_id

    def match(
        self,
        app_id: str,
        *,
        instance_hint: str | None = None,
        prefer_raw_app_id: bool = True,
        defer_wm_class_lookup: bool = False,
    ) -> str | None:
        """Map a runtime window identity to a Docking desktop ID.

        Args:
            app_id: Primary identity from the display server.
            instance_hint: Secondary identity when the display server
                provides a split identity model (currently only X11).
            prefer_raw_app_id: Whether raw compositor-style IDs should
                be tried before lowercase/X11-derived candidates.
            defer_wm_class_lookup: Whether to try all direct desktop ID
                candidates before consulting the launcher WM_CLASS index.

        Returns:
            The matching desktop ID (e.g. ``"firefox.desktop"``), or
            ``None`` when no match could be found.
        """
        app_id = app_id.strip()
        if not app_id:
            return None

        app_id_lower = app_id.lower().strip()

        # 1. Wine detection — when the primary identity is the generic
        #    "wine" class group, use the instance (exe path) instead.
        if instance_hint:
            result = self._match_wine_instance(
                app_id_lower=app_id_lower,
                instance_hint=instance_hint,
            )
            if result:
                return result

        # 2. Visible-item alias cache (fast path, no Gio calls).
        result = self._visible_aliases.get(_normalize_alias(app_id_lower))
        if result:
            return result

        # 3. Instance hint against visible aliases (X11 optimization:
        #    when class_group is generic but class_instance is specific
        #    and matches a pinned item).
        if instance_hint:
            result = self._visible_aliases.get(
                _normalize_alias(instance_hint.lower().strip())
            )
            if result:
                return result

        # 4. Candidate generation + resolution.
        candidates = self._candidates(
            app_id=app_id,
            app_id_lower=app_id_lower,
            instance_hint=instance_hint,
            prefer_raw_app_id=prefer_raw_app_id,
        )
        for candidate in candidates:
            # 4a. Visible aliases (normalized).
            result = self._visible_aliases.get(_normalize_alias(candidate))
            if result:
                return result

            # 4b. Direct desktop ID resolution (with missed-candidate
            #     memoization so signal bursts don't hammer Gio).
            desktop_id = _ensure_desktop_suffix(candidate)
            if not (
                self._cache_missed_desktop_ids and desktop_id in self._missed_candidates
            ):
                info = self._launcher.resolve(desktop_id, log_failures=False)
                if info is not None:
                    return info.desktop_id
                if self._cache_missed_desktop_ids:
                    self._missed_candidates.add(desktop_id)

            if defer_wm_class_lookup:
                continue

            # 4c. WM_CLASS index lookup (lazy-built once, then dict).
            info = self._launcher.resolve_by_wm_class(candidate)
            if info is not None:
                return info.desktop_id

        if defer_wm_class_lookup:
            for candidate in candidates:
                info = self._launcher.resolve_by_wm_class(candidate)
                if info is not None:
                    return info.desktop_id

        return None

    def _match_wine_instance(
        self, *, app_id_lower: str, instance_hint: str
    ) -> str | None:
        """Try to match a Wine window by its ``.exe`` instance name.

        Only triggers when *app_id_lower* is the generic ``"wine"``
        class group **and** the instance looks like a Windows executable
        path. Non-``.exe`` instances fall through to normal matching.
        """
        if app_id_lower != "wine":
            return None
        instance_lower = instance_hint.lower().strip()
        if not instance_lower.endswith(".exe"):
            return None
        for alias in _wine_aliases_from_instance(instance_hint):
            # Visible aliases (covers pinned Wine launcher items).
            desktop_id = self._visible_aliases.get(_normalize_alias(alias))
            if desktop_id:
                return desktop_id
            # Launcher WM_CLASS index.
            info = self._launcher.resolve_by_wm_class(alias)
            if info is not None:
                return info.desktop_id
        return None

    def _candidates(
        self,
        *,
        app_id: str,
        app_id_lower: str,
        instance_hint: str | None,
        prefer_raw_app_id: bool,
    ) -> list[str]:
        """Generate lookup candidates merged from both legacy matchers.

        Order is stable and deterministic. Wayland-style callers keep
        compositor-provided desktop IDs authoritative; X11 callers keep
        the old lowercase-first WM_CLASS order. Duplicates are removed
        while preserving first-occurrence order.
        """
        # X11-style candidates from class_group.
        x11_candidates = _class_group_candidates(
            class_lower=app_id_lower,
            class_group=app_id,
        )

        # Wayland-style candidates (dot-split, Snap prefixes, lowercase).
        wl_candidates = _app_id_candidates(app_id)

        # Keep raw compositor IDs first, then add X11 transforms and
        # Wayland/container fallbacks. Dedupe preserving order.
        seen: set[str] = set()
        merged: list[str] = []
        raw_candidates = [
            app_id,
            app_id.removesuffix(DESKTOP_SUFFIX),
            app_id_lower,
            app_id_lower.removesuffix(DESKTOP_SUFFIX),
        ]
        source_candidates = (
            raw_candidates + x11_candidates + wl_candidates
            if prefer_raw_app_id
            else x11_candidates + wl_candidates
        )
        for candidate in source_candidates:
            if candidate not in seen:
                seen.add(candidate)
                merged.append(candidate)

        # Instance hint aliases (when available) — appended after
        # the main candidates so they don't shadow a more specific match
        # from the class-group candidates.
        if instance_hint:
            for alias in _instance_candidates(instance_hint):
                if alias not in seen:
                    seen.add(alias)
                    merged.append(alias)

        return merged


def _instance_candidates(instance_hint: str) -> list[str]:
    """Generate lookup candidates from a WM_CLASS instance string."""
    instance_lower = instance_hint.lower().strip()
    if not instance_lower:
        return []
    candidates = [instance_lower]
    # Also handle the case where the instance itself contains spaces
    # (rare, but some X11 apps do this).
    if " " in instance_lower:
        candidates.append(instance_lower.replace(" ", "-"))
        candidates.append(instance_lower.replace(" ", ""))
    return list(dict.fromkeys(candidates))
