"""Canonical runtime application matching shared by every window backend."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from . import entries as desktop_entries
from .constants import DESKTOP_SUFFIX, FALLBACK_ICON, GNOME_APP_PREFIX
from .identity import ProcessIdentity, ProcessIdentityService
from .registry import ApplicationRegistry
from .types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationMatch,
    ApplicationOrigin,
    MatchEvidence,
    MatchMethod,
)

if TYPE_CHECKING:
    from docking.core.items import DockItem

APP_KIND = "app"


def _normalize_alias(value: str) -> str:
    """Normalize an alias for cache-key comparison."""
    return value.strip().lower().removesuffix(DESKTOP_SUFFIX)


def _ensure_desktop_suffix(value: str) -> str:
    """Append ``.desktop`` when *value* does not already have the suffix."""
    stripped = value.strip()
    return (
        stripped
        if stripped.lower().endswith(DESKTOP_SUFFIX)
        else f"{stripped}{DESKTOP_SUFFIX}"
    )


def _app_id_candidates(app_id: str) -> list[str]:
    """Generate stable Wayland-style app-ID candidates."""
    stripped = app_id.strip()
    if not stripped:
        return []
    candidates = [
        stripped,
        stripped.removesuffix(DESKTOP_SUFFIX),
        stripped.lower(),
        stripped.lower().removesuffix(DESKTOP_SUFFIX),
    ]
    if stripped.lower().endswith(".exe"):
        candidates.extend((stripped[:-4], stripped[:-4].lower()))
    elif "." in stripped:
        candidates.append(stripped.split(".")[-1])

    body = stripped.removesuffix(DESKTOP_SUFFIX)
    if "_" in body:
        segments = body.split("_")
        for index in range(len(segments) - 1):
            prefix = "_".join(segments[: index + 1])
            candidates.extend(
                (
                    prefix,
                    f"{prefix}{DESKTOP_SUFFIX}",
                    prefix.lower(),
                    f"{prefix.lower()}{DESKTOP_SUFFIX}",
                )
            )
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _class_group_candidates(*, class_lower: str, class_group: str) -> list[str]:
    """Generate stable X11 WM_CLASS candidates."""
    candidates = [class_lower]
    if " " in class_lower:
        candidates.extend(
            (
                class_lower.replace(" ", "-"),
                class_lower.replace(" ", ""),
            )
        )
    candidates.append(f"{GNOME_APP_PREFIX}{class_group}")
    return list(dict.fromkeys(candidates))


def _wine_aliases_from_instance(instance: str) -> list[str]:
    """Extract basename and suffix-free aliases from a Wine instance."""
    instance_lower = instance.lower().strip()
    basename = re.split(r"[\\/]", instance_lower)[-1]
    aliases = [basename]
    if basename.endswith(".exe"):
        aliases.append(basename[:-4])
    if instance_lower != basename:
        aliases.append(instance_lower)
    return list(dict.fromkeys(aliases))


@dataclass(frozen=True, slots=True)
class _VisibleAppIdentity:
    desktop_id: str
    application: ApplicationInfo | None
    name: str
    icon_name: str
    wm_class: str
    executable_path: Path | None


_SCRIPT_LAUNCHER_SUFFIXES = frozenset({".bash", ".sh", ".zsh"})
_GENERIC_BUNDLE_ROOTS = frozenset(
    {
        Path("/"),
        Path("/bin"),
        Path("/opt"),
        Path("/sbin"),
        Path("/usr"),
        Path("/usr/local"),
    }
)


def _application_executable_path(application: ApplicationInfo) -> Path | None:
    """Resolve the current executable target declared by an application."""
    return desktop_entries.executable_path_from_exec_line(application.exec_line)


def _executable_paths_conflict(
    launcher_path: Path | None,
    runtime_path: Path | None,
) -> bool:
    """Return whether two directly comparable executable paths disagree."""
    if launcher_path is None or runtime_path is None or launcher_path == runtime_path:
        return False
    if (
        launcher_path.name.casefold() == runtime_path.name.casefold()
        and _is_native_executable(launcher_path)
        and _is_native_executable(runtime_path)
    ):
        return True
    return _sibling_bundle_launchers_conflict(launcher_path, runtime_path)


def _sibling_bundle_launchers_conflict(
    launcher_path: Path,
    runtime_path: Path,
) -> bool:
    """Detect a wrapper/native pair from different sibling application roots."""
    if launcher_path.suffix.casefold() not in _SCRIPT_LAUNCHER_SUFFIXES:
        return False
    if launcher_path.stem.casefold() != runtime_path.name.casefold():
        return False
    launcher_root = _specific_bundle_root(launcher_path)
    runtime_root = _specific_bundle_root(runtime_path)
    return bool(
        launcher_root is not None
        and runtime_root is not None
        and launcher_root != runtime_root
        and launcher_root.parent == runtime_root.parent
        and _is_native_executable(runtime_path)
    )


def _specific_bundle_root(path: Path) -> Path | None:
    """Return a non-system application root for ``root/bin/executable``."""
    if path.parent.name not in {"bin", "sbin"}:
        return None
    root = path.parent.parent
    if root in _GENERIC_BUNDLE_ROOTS:
        return None
    if len(root.parts) == 3 and root.parts[1] == "home":
        return None
    return root


@lru_cache(maxsize=512)
def _is_native_executable(path: Path) -> bool:
    """Return whether *path* currently has the Linux ELF signature."""
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def _launcher_path_compatibility(
    launcher_path: Path | None,
    runtime_path: Path | None,
) -> int:
    """Score launchers that can legitimately replace themselves at runtime."""
    if launcher_path is None or runtime_path is None:
        return 0
    return 1 if not _is_native_executable(launcher_path) else 0


class AppIdMatcher:
    """Map backend runtime identity to canonical application matches."""

    def __init__(
        self,
        registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        *,
        cache_missed_desktop_ids: bool = False,
    ) -> None:
        self._registry = registry
        self._process_identity_service = process_identity_service
        self._cache_missed_desktop_ids = cache_missed_desktop_ids
        self._visible_aliases: dict[str, list[_VisibleAppIdentity]] = {}
        self._missed_candidates: set[str] = set()
        self._registry_generation = self._registry.generation

    @property
    def registry(self) -> ApplicationRegistry:
        """Return the exact registry borrowed by this matcher."""
        return self._registry

    @property
    def process_identity_service(self) -> ProcessIdentityService:
        """Return the exact process identity service borrowed by this matcher."""
        return self._process_identity_service

    def sync_visible_items(self, items: Iterable[DockItem]) -> None:
        """Rebuild application-only aliases from the current dock projection."""
        self._sync_registry_generation()
        self._visible_aliases.clear()
        for item in items:
            kind = getattr(item, "kind", None)
            if kind != APP_KIND:
                continue
            desktop_id = getattr(item, "desktop_id", "")
            if not isinstance(desktop_id, str) or not desktop_id:
                continue
            application = getattr(item, "application_info", None)
            if not isinstance(application, ApplicationInfo):
                application = self._registry.get(desktop_id)
            name = (
                application.name
                if application is not None
                else getattr(item, "name", "") or desktop_id
            )
            icon_name = (
                application.declared_icon
                if application is not None
                else getattr(item, "icon_name", "") or ""
            )
            wm_class = (
                application.wm_class
                if application is not None
                else getattr(item, "wm_class", "") or ""
            )
            executable_path = (
                _application_executable_path(application)
                if application is not None
                else desktop_entries.executable_path_from_exec_line(
                    getattr(item, "exec_line", "") or ""
                )
            )
            identity = _VisibleAppIdentity(
                desktop_id=desktop_id,
                application=application,
                name=name,
                icon_name=icon_name,
                wm_class=wm_class,
                executable_path=executable_path,
            )
            aliases = {
                desktop_id,
                desktop_id.removesuffix(DESKTOP_SUFFIX),
                wm_class,
            }
            for alias in aliases:
                normalized = _normalize_alias(alias)
                if not normalized:
                    continue
                matches = self._visible_aliases.setdefault(normalized, [])
                matches[:] = [
                    match
                    for match in matches
                    if match.desktop_id != identity.desktop_id
                ]
                matches.append(identity)

    def match(
        self,
        app_id: str,
        *,
        instance_hint: str | None = None,
        prefer_raw_app_id: bool = True,
        defer_wm_class_lookup: bool = False,
        process_id: int | None = None,
    ) -> str | None:
        """Return only the selected desktop ID."""
        result = self.match_result(
            app_id,
            instance_hint=instance_hint,
            prefer_raw_app_id=prefer_raw_app_id,
            defer_wm_class_lookup=defer_wm_class_lookup,
            process_id=process_id,
        )
        return result.desktop_id if result is not None else None

    def match_result(
        self,
        app_id: str,
        *,
        instance_hint: str | None = None,
        prefer_raw_app_id: bool = True,
        defer_wm_class_lookup: bool = False,
        process_id: int | None = None,
    ) -> ApplicationMatch | None:
        """Return the selected ID, canonical metadata, and exact evidence route."""
        self._sync_registry_generation()
        raw_app_id = app_id
        app_id = app_id.strip()
        if not app_id:
            return None
        app_id_lower = app_id.lower().strip()
        process = self._process_identity_service.identity_for_pid(process_id)

        if process is not None and process.launch is not None:
            desktop_id = process.launch.desktop_id
            return self._application_match(
                desktop_id=desktop_id,
                application=self._registry.get(desktop_id),
                method=MatchMethod.LAUNCH_PROVENANCE,
                raw_app_id=raw_app_id,
                instance_hint=instance_hint,
                process=process,
                process_id=process_id,
            )

        if instance_hint:
            result = self._match_wine_instance(
                app_id_lower=app_id_lower,
                instance_hint=instance_hint,
                raw_app_id=raw_app_id,
                process=process,
                process_id=process_id,
            )
            if result is not None:
                return result

        result = self._match_visible_alias(
            app_id_lower,
            method=MatchMethod.VISIBLE_ALIAS,
            raw_app_id=raw_app_id,
            instance_hint=instance_hint,
            process=process,
            process_id=process_id,
            runtime_wm_class=app_id,
        )
        if result is not None:
            return result

        if instance_hint:
            result = self._match_visible_alias(
                instance_hint,
                method=MatchMethod.INSTANCE_HINT,
                raw_app_id=raw_app_id,
                instance_hint=instance_hint,
                process=process,
                process_id=process_id,
                runtime_wm_class=app_id,
            )
            if result is not None:
                return result

        primary_candidates = self._candidates(
            app_id=app_id,
            app_id_lower=app_id_lower,
            instance_hint=None,
            prefer_raw_app_id=prefer_raw_app_id,
        )
        candidates = self._candidates(
            app_id=app_id,
            app_id_lower=app_id_lower,
            instance_hint=instance_hint,
            prefer_raw_app_id=prefer_raw_app_id,
        )
        primary_set = set(primary_candidates)
        for candidate in candidates:
            visible_method = (
                MatchMethod.VISIBLE_ALIAS
                if candidate in primary_set
                else MatchMethod.INSTANCE_HINT
            )
            result = self._match_visible_alias(
                candidate,
                method=visible_method,
                raw_app_id=raw_app_id,
                instance_hint=instance_hint,
                process=process,
                process_id=process_id,
                runtime_wm_class=app_id,
            )
            if result is not None:
                return result

            desktop_id = _ensure_desktop_suffix(candidate)
            if not (
                self._cache_missed_desktop_ids and desktop_id in self._missed_candidates
            ):
                application = self._registry.get(desktop_id)
                if application is not None:
                    return self._match_application_candidates(
                        (application,),
                        method=MatchMethod.DESKTOP_ID,
                        raw_app_id=raw_app_id,
                        instance_hint=instance_hint,
                        process=process,
                        process_id=process_id,
                        runtime_wm_class=app_id,
                    )
                if self._cache_missed_desktop_ids:
                    self._missed_candidates.add(desktop_id)

            if defer_wm_class_lookup:
                continue
            result = self._match_registry_alias(
                candidate,
                method=MatchMethod.WM_CLASS,
                raw_app_id=raw_app_id,
                instance_hint=instance_hint,
                process=process,
                process_id=process_id,
                runtime_wm_class=app_id,
            )
            if result is not None:
                return result

        if defer_wm_class_lookup:
            for candidate in candidates:
                result = self._match_registry_alias(
                    candidate,
                    method=MatchMethod.WM_CLASS,
                    raw_app_id=raw_app_id,
                    instance_hint=instance_hint,
                    process=process,
                    process_id=process_id,
                    runtime_wm_class=app_id,
                )
                if result is not None:
                    return result
        return None

    def _sync_registry_generation(self) -> None:
        generation = self._registry.generation
        if generation == self._registry_generation:
            return
        self._registry_generation = generation
        self._missed_candidates.clear()
        _is_native_executable.cache_clear()

    def _match_visible_alias(
        self,
        alias: str,
        *,
        method: MatchMethod,
        raw_app_id: str,
        instance_hint: str | None,
        process: ProcessIdentity | None,
        process_id: int | None,
        runtime_wm_class: str,
    ) -> ApplicationMatch | None:
        matches = self._visible_aliases.get(_normalize_alias(alias), ())
        if not matches:
            return None
        executable_path = process.executable_path if process is not None else None
        if executable_path is not None:
            exact = next(
                (
                    match
                    for match in reversed(matches)
                    if match.executable_path == executable_path
                ),
                None,
            )
            if exact is not None:
                return self._application_match(
                    desktop_id=exact.desktop_id,
                    application=exact.application,
                    method=method,
                    raw_app_id=raw_app_id,
                    instance_hint=instance_hint,
                    process=process,
                    process_id=process_id,
                )
        selected = max(
            enumerate(matches),
            key=lambda indexed: (
                _launcher_path_compatibility(
                    indexed[1].executable_path,
                    executable_path,
                ),
                indexed[0],
            ),
        )[1]
        if _executable_paths_conflict(selected.executable_path, executable_path):
            assert executable_path is not None
            return self._runtime_match(
                executable_path=executable_path,
                source=selected.application,
                name=selected.name,
                icon_name=selected.icon_name,
                wm_class=runtime_wm_class or selected.wm_class,
                raw_app_id=raw_app_id,
                instance_hint=instance_hint,
                process=process,
                process_id=process_id,
            )
        return self._application_match(
            desktop_id=selected.desktop_id,
            application=selected.application,
            method=method,
            raw_app_id=raw_app_id,
            instance_hint=instance_hint,
            process=process,
            process_id=process_id,
        )

    def _match_registry_alias(
        self,
        alias: str,
        *,
        method: MatchMethod,
        raw_app_id: str,
        instance_hint: str | None,
        process: ProcessIdentity | None,
        process_id: int | None,
        runtime_wm_class: str,
    ) -> ApplicationMatch | None:
        candidates = self._registry.resolve_all_by_wm_class(alias)
        return self._match_application_candidates(
            candidates,
            method=method,
            raw_app_id=raw_app_id,
            instance_hint=instance_hint,
            process=process,
            process_id=process_id,
            runtime_wm_class=runtime_wm_class,
        )

    def _match_application_candidates(
        self,
        candidates: tuple[ApplicationInfo, ...],
        *,
        method: MatchMethod,
        raw_app_id: str,
        instance_hint: str | None,
        process: ProcessIdentity | None,
        process_id: int | None,
        runtime_wm_class: str,
    ) -> ApplicationMatch | None:
        """Select and path-refine one ordered application candidate set."""
        if not candidates:
            return None
        resolved_candidates = tuple(
            (application, _application_executable_path(application))
            for application in candidates
        )
        executable_path = process.executable_path if process is not None else None
        if executable_path is not None:
            exact = next(
                (
                    application
                    for application, candidate_path in resolved_candidates
                    if candidate_path == executable_path
                ),
                None,
            )
            if exact is not None:
                return self._application_match(
                    desktop_id=exact.desktop_id,
                    application=exact,
                    method=method,
                    raw_app_id=raw_app_id,
                    instance_hint=instance_hint,
                    process=process,
                    process_id=process_id,
                )
        selected = max(
            enumerate(resolved_candidates),
            key=lambda indexed: (
                _launcher_path_compatibility(
                    indexed[1][1],
                    executable_path,
                ),
                -indexed[0],
            ),
        )[1]
        application, selected_path = selected
        if _executable_paths_conflict(selected_path, executable_path):
            assert executable_path is not None
            return self._runtime_match(
                executable_path=executable_path,
                source=application,
                name=application.name,
                icon_name=application.declared_icon,
                wm_class=runtime_wm_class or application.wm_class,
                raw_app_id=raw_app_id,
                instance_hint=instance_hint,
                process=process,
                process_id=process_id,
            )
        return self._application_match(
            desktop_id=application.desktop_id,
            application=application,
            method=method,
            raw_app_id=raw_app_id,
            instance_hint=instance_hint,
            process=process,
            process_id=process_id,
        )

    def _runtime_match(
        self,
        *,
        executable_path: Path,
        source: ApplicationInfo | None,
        name: str,
        icon_name: str,
        wm_class: str,
        raw_app_id: str,
        instance_hint: str | None,
        process: ProcessIdentity | None,
        process_id: int | None,
    ) -> ApplicationMatch:
        application = _runtime_application(
            executable_path=executable_path,
            source=source,
            name=name,
            icon_name=icon_name,
            wm_class=wm_class,
        )
        return self._application_match(
            desktop_id=application.desktop_id,
            application=application,
            method=MatchMethod.RUNTIME_PATH_SPLIT,
            raw_app_id=raw_app_id,
            instance_hint=instance_hint,
            process=process,
            process_id=process_id,
        )

    def _match_wine_instance(
        self,
        *,
        app_id_lower: str,
        instance_hint: str,
        raw_app_id: str,
        process: ProcessIdentity | None,
        process_id: int | None,
    ) -> ApplicationMatch | None:
        if app_id_lower != "wine":
            return None
        if not instance_hint.lower().strip().endswith(".exe"):
            return None
        for alias in _wine_aliases_from_instance(instance_hint):
            visible = self._visible_aliases.get(_normalize_alias(alias), ())
            if visible:
                selected = visible[-1]
                return self._application_match(
                    desktop_id=selected.desktop_id,
                    application=selected.application,
                    method=MatchMethod.WINE_INSTANCE,
                    raw_app_id=raw_app_id,
                    instance_hint=instance_hint,
                    process=process,
                    process_id=process_id,
                )
            installed = self._registry.resolve_all_by_wm_class(alias)
            if installed:
                selected = installed[0]
                return self._application_match(
                    desktop_id=selected.desktop_id,
                    application=selected,
                    method=MatchMethod.WINE_INSTANCE,
                    raw_app_id=raw_app_id,
                    instance_hint=instance_hint,
                    process=process,
                    process_id=process_id,
                )
        return None

    @staticmethod
    def _application_match(
        *,
        desktop_id: str,
        application: ApplicationInfo | None,
        method: MatchMethod,
        raw_app_id: str,
        instance_hint: str | None,
        process: ProcessIdentity | None,
        process_id: int | None,
    ) -> ApplicationMatch:
        return ApplicationMatch(
            desktop_id=desktop_id,
            application=application,
            evidence=MatchEvidence(
                method=method,
                raw_app_id=raw_app_id,
                instance_hint=instance_hint or "",
                pid=process.pid if process is not None else _evidence_pid(process_id),
                executable_path=(
                    process.executable_path if process is not None else None
                ),
            ),
        )

    def _candidates(
        self,
        *,
        app_id: str,
        app_id_lower: str,
        instance_hint: str | None,
        prefer_raw_app_id: bool,
    ) -> list[str]:
        """Merge legacy X11 and Wayland candidates without changing order."""
        x11_candidates = _class_group_candidates(
            class_lower=app_id_lower,
            class_group=app_id,
        )
        wayland_candidates = _app_id_candidates(app_id)
        raw_candidates = [
            app_id,
            app_id.removesuffix(DESKTOP_SUFFIX),
            app_id_lower,
            app_id_lower.removesuffix(DESKTOP_SUFFIX),
        ]
        source_candidates = (
            raw_candidates + x11_candidates + wayland_candidates
            if prefer_raw_app_id
            else x11_candidates + wayland_candidates
        )
        merged = list(dict.fromkeys(source_candidates))
        if instance_hint:
            merged.extend(
                candidate
                for candidate in _instance_candidates(instance_hint)
                if candidate not in merged
            )
        return merged


def _runtime_application(
    *,
    executable_path: Path,
    source: ApplicationInfo | None,
    name: str,
    icon_name: str,
    wm_class: str,
) -> ApplicationInfo:
    """Create deterministic canonical metadata for a path-split process."""
    desktop_id = desktop_entries.generated_desktop_id_for_path(executable_path)
    display_name = (
        name
        or (source.name if source is not None else "")
        or executable_path.stem
        or executable_path.name
    )
    declared_icon = (
        icon_name
        or (source.declared_icon if source is not None else "")
        or FALLBACK_ICON
    )
    runtime_wm_class = wm_class or (source.wm_class if source is not None else "")
    exec_line = str(executable_path)
    aliases = tuple(
        dict.fromkeys(
            (
                *desktop_entries.match_aliases(
                    desktop_id,
                    runtime_wm_class,
                    exec_line,
                ),
                *(source.aliases if source is not None else ()),
            )
        )
    )
    return ApplicationInfo(
        desktop_id=desktop_id,
        name=display_name,
        declared_icon=declared_icon,
        wm_class=runtime_wm_class,
        exec_line=exec_line,
        origin=ApplicationOrigin.RUNTIME,
        location=(
            source.location if source is not None else ApplicationLocation.SANDBOX
        ),
        desktop_file=None,
        executable_path=executable_path,
        aliases=aliases,
        visible=True,
        has_gio_source=False,
        generic_name=source.generic_name if source is not None else "",
        description=source.description if source is not None else "",
        categories=source.categories if source is not None else (),
        categories_raw=source.categories_raw if source is not None else "",
        keywords=source.keywords if source is not None else (),
        actions=source.actions if source is not None else (),
    )


def _evidence_pid(process_id: int | None) -> int | None:
    return (
        process_id
        if isinstance(process_id, int)
        and not isinstance(process_id, bool)
        and process_id > 0
        else None
    )


def _instance_candidates(instance_hint: str) -> list[str]:
    """Generate lookup candidates from a WM_CLASS instance string."""
    instance_lower = instance_hint.lower().strip()
    if not instance_lower:
        return []
    candidates = [instance_lower]
    if " " in instance_lower:
        candidates.extend(
            (
                instance_lower.replace(" ", "-"),
                instance_lower.replace(" ", ""),
            )
        )
    return list(dict.fromkeys(candidates))


__all__ = [
    "AppIdMatcher",
    "ApplicationMatch",
    "_app_id_candidates",
    "_class_group_candidates",
    "_ensure_desktop_suffix",
    "_executable_paths_conflict",
    "_instance_candidates",
    "_is_native_executable",
    "_launcher_path_compatibility",
    "_normalize_alias",
    "_runtime_application",
    "_sibling_bundle_launchers_conflict",
    "_specific_bundle_root",
    "_wine_aliases_from_instance",
    "desktop_entries",
]
