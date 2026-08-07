"""Compatibility adapter for the canonical application matcher."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from docking.platform.applications import entries as desktop_entries
from docking.platform.applications.identity import (
    ProcessIdentity,
    ProcessIdentityService,
)
from docking.platform.applications.matcher import (
    AppIdMatcher as _CanonicalAppIdMatcher,
)
from docking.platform.applications.matcher import (
    _app_id_candidates,
    _class_group_candidates,
    _ensure_desktop_suffix,
    _executable_paths_conflict,
    _instance_candidates,
    _is_native_executable,
    _launcher_path_compatibility,
    _normalize_alias,
    _runtime_application,
    _sibling_bundle_launchers_conflict,
    _specific_bundle_root,
    _wine_aliases_from_instance,
)
from docking.platform.applications.registry import ApplicationRegistry
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationMatch,
    ApplicationOrigin,
    MatchEvidence,
    MatchMethod,
)
from docking.platform.process_identity import identity_for_pid
from docking.platform.running import (
    RuntimeAppIdentity,
    _canonical_runtime_application,
    _legacy_runtime_identity,
)

if TYPE_CHECKING:
    from docking.core.items import DockItem


class _LegacyRegistryAdapter:
    """Project a legacy Launcher-like resolver into the registry contract."""

    def __init__(self, launcher: object) -> None:
        self._launcher = launcher

    @property
    def generation(self) -> int:
        registry = getattr(self._launcher, "_registry", None)
        generation = getattr(registry, "generation", 0)
        return generation if isinstance(generation, int) else 0

    def get(self, desktop_id: str) -> ApplicationInfo | None:
        return self.resolve(desktop_id, log_failures=False)

    def resolve(
        self,
        desktop_id: str,
        *,
        log_failures: bool = True,
    ) -> ApplicationInfo | None:
        resolve = getattr(self._launcher, "resolve", None)
        if not callable(resolve):
            return None
        return _application_from_legacy(resolve(desktop_id, log_failures=log_failures))

    def resolve_all_by_wm_class(
        self,
        alias: str,
    ) -> tuple[ApplicationInfo, ...]:
        resolve_all = getattr(self._launcher, "resolve_all_by_wm_class", None)
        if callable(resolve_all):
            infos = resolve_all(alias)
            if isinstance(infos, list | tuple):
                return tuple(
                    application
                    for info in infos
                    if (application := _application_from_legacy(info)) is not None
                )
        resolve_one = getattr(self._launcher, "resolve_by_wm_class", None)
        info = resolve_one(alias) if callable(resolve_one) else None
        application = _application_from_legacy(info)
        return (application,) if application is not None else ()


class _LegacyProcessIdentityService:
    """Route lookups through the monkeypatchable compatibility function."""

    @staticmethod
    def identity_for_pid(pid: int | None) -> ProcessIdentity | None:
        return identity_for_pid(pid)


class _LegacyVisibleItem:
    """Add the canonical application kind to migration-era item values."""

    kind = "app"

    def __init__(self, item: object) -> None:
        self._item = item
        self.application_info = _application_from_legacy(item)

    def __getattr__(self, name: str) -> object:
        return getattr(self._item, name)


def _application_from_legacy(info: object | None) -> ApplicationInfo | None:
    if isinstance(info, ApplicationInfo):
        return info
    desktop_id = getattr(info, "desktop_id", None)
    if not isinstance(desktop_id, str) or not desktop_id:
        return None
    name = getattr(info, "name", "") or desktop_id
    icon_name = getattr(info, "icon_name", "") or ""
    wm_class = getattr(info, "wm_class", "") or ""
    exec_line = getattr(info, "exec_line", "") or ""
    return ApplicationInfo(
        desktop_id=desktop_id,
        name=name,
        declared_icon=icon_name,
        wm_class=wm_class,
        exec_line=exec_line,
        origin=(
            ApplicationOrigin.GENERATED
            if desktop_id.startswith(desktop_entries.GENERATED_DESKTOP_PREFIX)
            else ApplicationOrigin.INSTALLED
        ),
        location=ApplicationLocation.SANDBOX,
        desktop_file=None,
        executable_path=desktop_entries.executable_path_from_exec_line(exec_line),
        aliases=tuple(
            desktop_entries.match_aliases(
                desktop_id=desktop_id,
                wm_class=wm_class,
                exec_line=exec_line,
            )
        ),
        visible=True,
        has_gio_source=False,
    )


class AppIdMatcher(_CanonicalAppIdMatcher):
    """Accept the old Launcher constructor while delegating matching."""

    def __init__(
        self,
        registry: ApplicationRegistry | None = None,
        process_identity_service: ProcessIdentityService | None = None,
        *,
        cache_missed_desktop_ids: bool = False,
        launcher: object | None = None,
    ) -> None:
        if (
            launcher is None
            and process_identity_service is None
            and registry is not None
            and not isinstance(registry, ApplicationRegistry)
        ):
            launcher = registry
            registry = None
        self._compatibility_items = registry is None
        if registry is None:
            if launcher is None:
                raise TypeError("registry and process_identity_service are required")
            candidate = getattr(launcher, "registry", None)
            canonical_registry = (
                candidate
                if isinstance(candidate, ApplicationRegistry)
                else cast(ApplicationRegistry, _LegacyRegistryAdapter(launcher))
            )
            canonical_process_identity_service = process_identity_service or cast(
                ProcessIdentityService, _LegacyProcessIdentityService()
            )
        elif process_identity_service is None:
            raise TypeError("process_identity_service is required with registry")
        else:
            canonical_registry = registry
            canonical_process_identity_service = process_identity_service

        super().__init__(
            registry=canonical_registry,
            process_identity_service=canonical_process_identity_service,
            cache_missed_desktop_ids=cache_missed_desktop_ids,
        )

    def sync_visible_items(self, items: Iterable[object]) -> None:
        if self._compatibility_items:
            items = (
                item
                if getattr(item, "kind", None) is not None
                else _LegacyVisibleItem(item)
                for item in items
            )
        super().sync_visible_items(cast("Iterable[DockItem]", items))

    def match_result(
        self,
        app_id: str,
        *,
        instance_hint: str | None = None,
        prefer_raw_app_id: bool = True,
        defer_wm_class_lookup: bool = False,
        process_id: int | None = None,
    ) -> AppMatch | None:  # ty: ignore[invalid-method-override]
        """Return the historical match shape with canonical evidence attached."""
        result = super().match_result(
            app_id,
            instance_hint=instance_hint,
            prefer_raw_app_id=prefer_raw_app_id,
            defer_wm_class_lookup=defer_wm_class_lookup,
            process_id=process_id,
        )
        return AppMatch.from_canonical(result) if result is not None else None


@dataclass(frozen=True, init=False)
class AppMatch:
    """Historical two-field result with canonical diagnostics attached."""

    desktop_id: str
    runtime_app: RuntimeAppIdentity | None = None

    def __init__(
        self,
        desktop_id: str,
        runtime_app: RuntimeAppIdentity | ApplicationInfo | None = None,
    ) -> None:
        canonical_application = _canonical_runtime_application(runtime_app)
        legacy_runtime = (
            runtime_app
            if isinstance(runtime_app, RuntimeAppIdentity)
            else _legacy_runtime_identity(canonical_application)
        )
        object.__setattr__(self, "desktop_id", desktop_id)
        object.__setattr__(self, "runtime_app", legacy_runtime)
        object.__setattr__(self, "_application", canonical_application)
        object.__setattr__(
            self,
            "_evidence",
            MatchEvidence(
                method=MatchMethod.DESKTOP_ID,
                raw_app_id=desktop_id,
            ),
        )

    @classmethod
    def from_canonical(cls, match: ApplicationMatch) -> AppMatch:
        """Adapt one canonical match without discarding metadata or evidence."""
        result = cls(
            match.desktop_id,
            match.application,
        )
        object.__setattr__(result, "_evidence", match.evidence)
        return result

    @property
    def application(self) -> ApplicationInfo | None:
        """Retain canonical metadata for migration-era diagnostics."""
        return self._application

    @property
    def evidence(self) -> MatchEvidence:
        """Retain the canonical evidence route for current compatibility users."""
        return self._evidence


__all__ = [
    "AppIdMatcher",
    "AppMatch",
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
    "identity_for_pid",
]
