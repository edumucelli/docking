from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from docking.core.items import APP_KIND, APPLET_KIND, FILE_KIND, DockItem
from docking.platform.applications.identity import (
    LaunchProvenance,
    ProcessIdentity,
)
from docking.platform.applications.matcher import AppIdMatcher, _is_native_executable
from docking.platform.applications.types import (
    ApplicationAction,
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
    MatchMethod,
)


class _Registry:
    def __init__(self) -> None:
        self.generation = 1
        self.records: dict[str, ApplicationInfo] = {}
        self.aliases: dict[str, tuple[ApplicationInfo, ...]] = {}

    def get(self, desktop_id: str) -> ApplicationInfo | None:
        return self.records.get(desktop_id)

    def resolve_all_by_wm_class(
        self,
        alias: str,
    ) -> tuple[ApplicationInfo, ...]:
        return self.aliases.get(alias.lower().strip(), ())

    def publish(self, *applications: ApplicationInfo) -> None:
        self.records = {
            application.desktop_id: application for application in applications
        }
        aliases: dict[str, list[ApplicationInfo]] = {}
        for application in applications:
            for alias in application.aliases:
                aliases.setdefault(alias.lower(), []).append(application)
        self.aliases = {key: tuple(values) for key, values in aliases.items()}
        self.generation += 1


class _Processes:
    def __init__(self) -> None:
        self.identity: ProcessIdentity | None = None

    def identity_for_pid(self, pid: int | None) -> ProcessIdentity | None:
        del pid
        return self.identity


def _application(
    desktop_id: str,
    *,
    wm_class: str = "",
    executable_path: Path | None = None,
    name: str = "Application",
    icon: str = "application-icon",
) -> ApplicationInfo:
    aliases = tuple(
        alias
        for alias in (
            desktop_id.removesuffix(".desktop").lower(),
            wm_class.lower(),
        )
        if alias
    )
    return ApplicationInfo(
        desktop_id=desktop_id,
        name=name,
        declared_icon=icon,
        wm_class=wm_class,
        exec_line=str(executable_path) if executable_path is not None else "",
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.SANDBOX,
        desktop_file=Path("/applications") / desktop_id,
        executable_path=executable_path,
        aliases=aliases,
        visible=True,
        has_gio_source=True,
    )


def test_evidence_routes_and_removed_launch_provenance() -> None:
    registry = _Registry()
    processes = _Processes()
    application = _application("org.example.App.desktop", wm_class="Example")
    registry.publish(application)
    matcher = AppIdMatcher(registry, processes)

    direct = matcher.match_result("org.example.App")
    alias = matcher.match_result("Example")
    matcher.sync_visible_items(
        [
            DockItem(
                desktop_id=application.desktop_id,
                kind=APP_KIND,
                application_info=application,
            )
        ]
    )
    visible = matcher.match_result("Example")
    instance = matcher.match_result("unknown", instance_hint="Example")
    wine = matcher.match_result("wine", instance_hint=r"C:\Example.exe")

    assert direct is not None
    assert direct.evidence.method is MatchMethod.DESKTOP_ID
    assert alias is not None
    assert alias.evidence.method is MatchMethod.WM_CLASS
    assert visible is not None
    assert visible.evidence.method is MatchMethod.VISIBLE_ALIAS
    assert instance is not None
    assert instance.evidence.method is MatchMethod.INSTANCE_HINT
    assert wine is not None
    assert wine.evidence.method is MatchMethod.WINE_INSTANCE

    processes.identity = ProcessIdentity(
        pid=41,
        executable_path=Path("/opt/removed"),
        launch=LaunchProvenance(desktop_id="removed.desktop"),
    )
    provenance = matcher.match_result("unknown", process_id=41)

    assert provenance is not None
    assert provenance.desktop_id == "removed.desktop"
    assert provenance.application is None
    assert provenance.evidence.method is MatchMethod.LAUNCH_PROVENANCE


def test_visible_aliases_only_index_applications_and_reuse_metadata() -> None:
    registry = _Registry()
    processes = _Processes()
    application = _application("visible.desktop", wm_class="VisibleAlias")
    matcher = AppIdMatcher(registry, processes)
    matcher.sync_visible_items(
        [
            DockItem(
                desktop_id="applet.desktop",
                kind=APPLET_KIND,
                wm_class="AppletAlias",
            ),
            DockItem(
                desktop_id="file.desktop",
                kind=FILE_KIND,
                wm_class="FileAlias",
            ),
            DockItem(
                desktop_id=application.desktop_id,
                kind=APP_KIND,
                application_info=application,
            ),
        ]
    )

    result = matcher.match_result("VisibleAlias")

    assert matcher.match_result("AppletAlias") is None
    assert matcher.match_result("FileAlias") is None
    assert result is not None
    assert result.application is application


def test_direct_id_conflict_creates_runtime_path_split(
    tmp_path: Path,
) -> None:
    installed_path = tmp_path / "installed" / "bin" / "tool"
    running_path = tmp_path / "running" / "bin" / "tool"
    installed_path.parent.mkdir(parents=True)
    running_path.parent.mkdir(parents=True)
    installed_path.write_bytes(b"\x7fELF")
    running_path.write_bytes(b"\x7fELF")
    application = _application(
        "tool.desktop",
        wm_class="SharedTool",
        executable_path=installed_path,
    )
    registry = _Registry()
    registry.publish(application)
    processes = _Processes()
    processes.identity = ProcessIdentity(pid=42, executable_path=running_path)
    matcher = AppIdMatcher(registry, processes)

    result = matcher.match_result("tool", process_id=42)

    assert result is not None
    assert result.desktop_id != application.desktop_id
    assert result.application is not None
    assert result.application.origin is ApplicationOrigin.RUNTIME
    assert result.application.executable_path == running_path
    assert result.evidence.method is MatchMethod.RUNTIME_PATH_SPLIT


def test_wine_ignores_conflicting_process_path_and_preserves_tie_breaks(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first" / "bin" / "tool"
    second_path = tmp_path / "second" / "bin" / "tool"
    running_path = tmp_path / "running" / "bin" / "tool"
    for path in (first_path, second_path, running_path):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\x7fELF")
    first = _application(
        "first.desktop",
        wm_class="Tool",
        executable_path=first_path,
    )
    second = _application(
        "second.desktop",
        wm_class="Tool",
        executable_path=second_path,
    )
    registry = _Registry()
    registry.publish(first, second)
    processes = _Processes()
    processes.identity = ProcessIdentity(pid=57, executable_path=running_path)
    visible_matcher = AppIdMatcher(registry, processes)
    visible_matcher.sync_visible_items(
        [
            DockItem(
                desktop_id=first.desktop_id,
                kind=APP_KIND,
                application_info=first,
            ),
            DockItem(
                desktop_id=second.desktop_id,
                kind=APP_KIND,
                application_info=second,
            ),
        ]
    )

    visible = visible_matcher.match_result(
        "wine",
        instance_hint=r"C:\Games\Tool.exe",
        process_id=57,
    )
    installed = AppIdMatcher(registry, processes).match_result(
        "wine",
        instance_hint=r"C:\Games\Tool.exe",
        process_id=57,
    )

    assert visible is not None
    assert visible.application is second
    assert visible.runtime_app is None
    assert visible.evidence.method is MatchMethod.WINE_INSTANCE
    assert visible.evidence.executable_path == running_path
    assert installed is not None
    assert installed.application is first
    assert installed.runtime_app is None
    assert installed.evidence.method is MatchMethod.WINE_INSTANCE
    assert installed.evidence.executable_path == running_path


def test_runtime_split_is_stable_and_preserves_source_metadata(
    tmp_path: Path,
) -> None:
    installed_path = tmp_path / "v1" / "bin" / "tool"
    running_path = tmp_path / "v2" / "bin" / "tool"
    installed_path.parent.mkdir(parents=True)
    running_path.parent.mkdir(parents=True)
    installed_path.write_bytes(b"\x7fELF")
    running_path.write_bytes(b"\x7fELF")
    action = ApplicationAction(
        action_id="new-window",
        name="New Window",
        sources=frozenset(),
    )
    source = replace(
        _application(
            "tool.desktop",
            wm_class="SharedTool",
            executable_path=installed_path,
            name="Shared Tool",
            icon="shared-tool",
        ),
        location=ApplicationLocation.HOST,
        generic_name="Tool",
        description="Source description",
        categories=("Utility",),
        categories_raw="Utility;",
        keywords=("shared",),
        actions=(action,),
    )
    registry = _Registry()
    registry.publish(source)
    processes = _Processes()
    processes.identity = ProcessIdentity(pid=43, executable_path=running_path)
    matcher = AppIdMatcher(registry, processes)
    matcher.sync_visible_items(
        [
            DockItem(
                desktop_id=source.desktop_id,
                kind=APP_KIND,
                application_info=source,
            )
        ]
    )

    first = matcher.match_result("SharedTool", process_id=43)
    second = matcher.match_result("SharedTool", process_id=43)

    assert first is not None
    assert second is not None
    assert first.application == second.application
    runtime = first.application
    assert runtime is not None
    assert runtime.desktop_id == second.desktop_id
    assert runtime.origin is ApplicationOrigin.RUNTIME
    assert runtime.desktop_file is None
    assert runtime.executable_path == running_path
    assert runtime.exec_line == str(running_path)
    assert runtime.name == source.name
    assert runtime.declared_icon == source.declared_icon
    assert runtime.location is source.location
    assert runtime.generic_name == source.generic_name
    assert runtime.description == source.description
    assert runtime.categories == source.categories
    assert runtime.keywords == source.keywords
    assert runtime.actions == source.actions


def test_symlink_retarget_is_observed_without_registry_generation(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first" / "bin" / "tool"
    second = tmp_path / "second" / "bin" / "tool"
    launcher = tmp_path / "launcher"
    for path in (first, second):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\x7fELF")
    launcher.symlink_to(first)
    application = replace(
        _application(
            "tool.desktop",
            wm_class="SharedTool",
            executable_path=launcher,
        ),
        executable_path=first.resolve(),
    )
    registry = _Registry()
    registry.publish(application)
    generation = registry.generation
    processes = _Processes()
    processes.identity = ProcessIdentity(pid=58, executable_path=second.resolve())
    matcher = AppIdMatcher(registry, processes)

    launcher.unlink()
    launcher.symlink_to(second)
    direct = matcher.match_result("tool", process_id=58)
    matcher.sync_visible_items(
        [
            DockItem(
                desktop_id=application.desktop_id,
                kind=APP_KIND,
                application_info=application,
            )
        ]
    )
    visible = matcher.match_result("SharedTool", process_id=58)

    assert registry.generation == generation
    assert direct is not None
    assert direct.application is application
    assert direct.evidence.method is MatchMethod.DESKTOP_ID
    assert visible is not None
    assert visible.application is application
    assert visible.evidence.method is MatchMethod.VISIBLE_ALIAS


def test_registry_generation_invalidates_misses_and_native_symlink_cache(
    tmp_path: Path,
) -> None:
    registry = _Registry()
    processes = _Processes()
    matcher = AppIdMatcher(
        registry,
        processes,
        cache_missed_desktop_ids=True,
    )
    assert matcher.match_result("new-application") is None
    assert "new-application.desktop" in matcher._missed_candidates

    application = _application("new-application.desktop")
    registry.publish(application)
    resolved = matcher.match_result("new-application")

    assert resolved is not None
    assert resolved.application is application
    assert not matcher._missed_candidates

    script = tmp_path / "script"
    native = tmp_path / "native"
    executable = tmp_path / "current"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    native.write_bytes(b"\x7fELF")
    executable.symlink_to(script)
    _is_native_executable.cache_clear()
    assert not _is_native_executable(executable)
    executable.unlink()
    executable.symlink_to(native)
    assert not _is_native_executable(executable)

    registry.generation += 1
    matcher.match_result("missing")

    assert _is_native_executable(executable)
