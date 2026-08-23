"""Deterministic tests for the installed-application registry."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Thread, get_ident

import pytest

import docking.platform.applications.registry as registry_mod
from docking.platform.applications.projections import search_metadata
from docking.platform.applications.registry import (
    DEFAULT_DEBOUNCE_MS,
    ApplicationRegistry,
    UnidentifiedApplicationListing,
)
from docking.platform.applications.types import (
    ActionSource,
    ApplicationLocation,
    ApplicationOrigin,
)


class _Icon:
    def __init__(self, value: str) -> None:
        self.value = value

    def to_string(self) -> str:
        return self.value


class _GioApplication:
    def __init__(
        self,
        desktop_id: str,
        *,
        name: str = "",
        filename: str = "",
        icon: str = "",
        wm_class: str = "",
        commandline: str = "",
        categories: str = "",
        generic_name: str = "",
        description: str = "",
        keywords: tuple[str, ...] = (),
        actions: dict[str, str] | None = None,
        hidden: bool = False,
        no_display: bool = False,
    ) -> None:
        self.desktop_id = desktop_id
        self.name = name
        self.filename = filename
        self.icon = icon
        self.wm_class = wm_class
        self.commandline = commandline
        self.categories = categories
        self.generic_name = generic_name
        self.description = description
        self.keywords = keywords
        self.actions = actions or {}
        self.hidden = hidden
        self.no_display = no_display

    def get_id(self) -> str:
        return self.desktop_id

    def get_display_name(self) -> str:
        return self.name

    def get_filename(self) -> str:
        return self.filename

    def get_icon(self) -> _Icon | None:
        return _Icon(self.icon) if self.icon else None

    def get_startup_wm_class(self) -> str:
        return self.wm_class

    def get_commandline(self) -> str:
        return self.commandline

    def get_categories(self) -> str:
        return self.categories

    def get_generic_name(self) -> str:
        return self.generic_name

    def get_description(self) -> str:
        return self.description

    def get_keywords(self) -> tuple[str, ...]:
        return self.keywords

    def list_actions(self) -> tuple[str, ...]:
        return tuple(self.actions)

    def get_action_name(self, action_id: str) -> str:
        return self.actions[action_id]

    def get_is_hidden(self) -> bool:
        return self.hidden

    def get_nodisplay(self) -> bool:
        return self.no_display


class _SignalSource:
    def __init__(self) -> None:
        self.callbacks: dict[int, Callable[..., object]] = {}
        self.disconnected: list[int] = []

    def connect(self, signal: str, callback: Callable[..., object]) -> int:
        assert signal == "changed"
        handler_id = max(self.callbacks, default=0) + 1
        self.callbacks[handler_id] = callback
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        self.disconnected.append(handler_id)
        self.callbacks.pop(handler_id, None)

    def emit_changed(self) -> None:
        for callback in tuple(self.callbacks.values()):
            callback(self)


class _DirectoryMonitor(_SignalSource):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _Scheduler:
    def __init__(self) -> None:
        self.callbacks: dict[int, Callable[[], bool]] = {}
        self.delays: list[int] = []
        self.cancelled: list[int] = []

    def schedule(self, delay: int, callback: Callable[[], bool]) -> int:
        source_id = max(self.callbacks, default=0) + 1
        self.callbacks[source_id] = callback
        self.delays.append(delay)
        return source_id

    def cancel(self, source_id: int) -> None:
        self.cancelled.append(source_id)

    def run(self, source_id: int) -> bool:
        return self.callbacks[source_id]()


def _registry(
    *,
    applications: list[object] | None = None,
    directories: list[Path] | None = None,
) -> ApplicationRegistry:
    registry = ApplicationRegistry(
        application_source=lambda: list(applications or ()),
        desktop_directories_source=lambda: list(directories or ()),
    )
    registry._desktop_app_info_from_filename = lambda _path: None
    return registry


def _write_desktop(
    path: Path,
    *,
    name: str,
    exec_line: str = "example-app",
    extra: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\n{extra}",
        encoding="utf-8",
    )


def test_successful_empty_initial_discovery_publishes_generation():
    registry = _registry()

    assert registry.generation == 0
    assert registry.refresh() is True
    assert registry.generation == 1
    assert registry.snapshot() == ()


def test_failed_initial_discovery_leaves_registry_unready():
    registry = _registry()

    def fail_discovery():
        raise RuntimeError("discovery failed")

    registry._application_source = fail_discovery

    assert registry.refresh() is False
    assert registry.generation == 0


def test_default_gio_source_filters_non_desktop_app_infos_but_injection_does_not(
    monkeypatch,
):
    desktop = _GioApplication(
        "desktop.desktop",
        name="Desktop",
        commandline="desktop",
    )
    generic = _GioApplication(
        "generic.desktop",
        name="Generic",
        commandline="generic",
    )
    monkeypatch.setattr(
        registry_mod.Gio.AppInfo,
        "get_all",
        lambda: [generic, desktop],
    )
    monkeypatch.setattr(
        registry_mod,
        "_is_gio_desktop_app_info",
        lambda app_info: app_info is desktop,
    )
    default_registry = ApplicationRegistry(
        desktop_directories_source=list,
    )
    default_registry._desktop_app_info_from_filename = lambda _path: None

    assert default_registry.refresh() is True
    assert default_registry.get("desktop.desktop") is not None
    assert default_registry.get("generic.desktop") is None

    injected_registry = _registry(applications=[generic])
    assert injected_registry.refresh() is True
    assert injected_registry.get("generic.desktop") is not None


def test_idless_gio_entries_have_distinct_listing_side_table_records():
    first = _GioApplication(
        "",
        name="ID-less",
        commandline="first",
        categories="Utility;",
        icon="first-icon",
        description="First description",
        generic_name="First generic name",
    )
    second = _GioApplication(
        "",
        name="ID-less",
        commandline="second",
        categories="Development;",
        icon="second-icon",
    )
    hidden = _GioApplication("", name="Hidden", hidden=True)
    no_display = _GioApplication("", name="No Display", no_display=True)
    registry = _registry(
        applications=[first, second, hidden, no_display],
    )

    assert registry.refresh() is True

    listings = registry.unidentified_snapshot()
    assert [listing.name for listing in listings] == ["ID-less", "ID-less"]
    assert [listing.categories for listing in listings] == [
        "Utility;",
        "Development;",
    ]
    assert len({listing.listing_key for listing in listings}) == 2
    assert listings[0].exec_line == "first"
    assert listings[0].description == "First description"
    assert listings[0].generic_name == "First generic name"
    assert registry._gio_handle_for_unidentified(listings[0].listing_key) is first
    assert registry._gio_handle_for_unidentified(listings[1].listing_key) is second
    assert registry.get("") is None
    assert registry.snapshot() == ()
    assert registry.resolvable_snapshot() == ()


def test_idless_tokens_rotate_without_public_change_and_reject_stale_rows():
    first = _GioApplication(
        "",
        name="Indistinguishable",
        commandline="same-command",
        categories="Utility;",
        icon="same-icon",
        description="Same description",
        generic_name="Same generic name",
    )
    second = _GioApplication(
        "",
        name="Indistinguishable",
        commandline="same-command",
        categories="Utility;",
        icon="same-icon",
        description="Same description",
        generic_name="Same generic name",
    )
    applications: list[object] = [first, second]
    registry = _registry(applications=applications)
    notifications: list[int] = []
    registry.add_listener(lambda: notifications.append(registry.generation))

    assert registry.refresh() is True
    generation = registry.generation
    previous = registry.unidentified_snapshot()
    stale_keys = tuple(listing.listing_key for listing in previous)

    applications[:] = [second, first]
    assert registry.refresh() is False

    current = registry.unidentified_snapshot()
    current_keys = tuple(listing.listing_key for listing in current)
    assert current == previous
    assert current is not previous
    assert current_keys != stale_keys
    assert registry.generation == generation
    assert notifications == [generation]
    assert all(
        registry._gio_handle_for_unidentified(listing_key) is None
        for listing_key in stale_keys
    )
    assert registry._gio_handle_for_unidentified(current_keys[0]) is second
    assert registry._gio_handle_for_unidentified(current_keys[1]) is first


def test_unchanged_refresh_atomically_replaces_private_gio_handles():
    first = _GioApplication(
        "stable.desktop",
        name="Stable",
        commandline="stable",
    )
    replacement = _GioApplication(
        "stable.desktop",
        name="Stable",
        commandline="stable",
    )
    applications: list[object] = [first]
    registry = _registry(applications=applications)
    notifications: list[int] = []
    registry.add_listener(lambda: notifications.append(registry.generation))

    assert registry.refresh() is True
    generation = registry.generation
    snapshot = registry.snapshot()
    assert registry._gio_handle_for("stable.desktop") is first

    applications[0] = replacement

    assert registry.refresh() is False
    assert registry.generation == generation
    assert registry.snapshot() is snapshot
    assert notifications == [generation]
    assert registry._gio_handle_for("stable.desktop") is replacement


def test_file_discovery_preserves_visibility_precedence_order_and_indexes(tmp_path):
    first = tmp_path / "first" / "applications"
    second = tmp_path / "second" / "applications"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    executable = tmp_path / "bin" / "shared-tool"
    executable.parent.mkdir()
    executable.write_bytes(b"\x7fELF")

    _write_desktop(
        first / "a-shared.desktop",
        name="Zulu Shared",
        exec_line=str(executable),
        extra=(
            "StartupWMClass=SharedTool\n"
            "Icon=shared-tool\n"
            "Actions=open-special;\n"
            "\n"
            "[Desktop Action open-special]\n"
            "Name=Open Special\n"
            "Exec=shared-tool --special\n"
        ),
    )
    _write_desktop(
        first / "b-shared.desktop",
        name="Alpha Shared",
        exec_line=str(executable),
        extra="StartupWMClass=SharedTool\n",
    )
    _write_desktop(
        first / "duplicate.desktop",
        name="First Source",
        exec_line="first-source",
    )
    _write_desktop(
        second / "duplicate.desktop",
        name="Second Source",
        exec_line="second-source",
    )
    _write_desktop(
        first / "hidden-duplicate.desktop",
        name="Hidden Override",
        extra="Hidden=true\n",
    )
    _write_desktop(
        second / "hidden-duplicate.desktop",
        name="Lower Visible Source",
    )
    _write_desktop(
        first / "no-display.desktop",
        name="Direct Only",
        extra="NoDisplay=true\n",
    )
    _write_desktop(first / "no-icon.desktop", name="No Icon")

    registry = _registry(directories=[first, second])

    assert registry.refresh() is True
    assert registry.refresh() is False
    assert registry.generation == 1

    assert registry.get("hidden-duplicate.desktop") is None
    direct_only = registry.resolve("no-display.desktop")
    assert direct_only is not None
    assert direct_only.visible is False
    assert "no-display.desktop" not in {
        application.desktop_id for application in registry.snapshot()
    }

    duplicate = registry.get("duplicate.desktop")
    assert duplicate is not None
    assert duplicate.name == "First Source"
    assert duplicate.desktop_file == first / "duplicate.desktop"
    assert registry.resolve_by_desktop_file(first / "duplicate.desktop") is duplicate
    desktop_alias = tmp_path / "duplicate-link.desktop"
    desktop_alias.symlink_to(first / "duplicate.desktop")
    assert registry.resolve_by_desktop_file(desktop_alias) is duplicate

    no_icon = registry.get("no-icon.desktop")
    assert no_icon is not None
    assert no_icon.declared_icon == ""

    shared = registry.resolve_all_by_wm_class(" SharedTool ")
    assert [application.desktop_id for application in shared] == [
        "a-shared.desktop",
        "b-shared.desktop",
    ]
    assert registry.resolve_by_wm_class("sharedtool") is shared[0]
    by_executable = registry.resolve_all_by_executable_path(executable)
    assert [application.desktop_id for application in by_executable] == [
        "a-shared.desktop",
        "b-shared.desktop",
    ]
    action = shared[0].actions[0]
    assert action.action_id == "open-special"
    assert action.sources == frozenset({ActionSource.DESKTOP_FILE})
    assert action.file_exec_line == "shared-tool --special"
    assert [application.name for application in registry.snapshot()] == sorted(
        (application.name for application in registry.snapshot()),
        key=str.casefold,
    )

    with pytest.raises(TypeError):
        registry.applications_by_id["other.desktop"] = shared[0]  # type: ignore[index]


def test_alias_candidates_follow_directory_source_order_not_snapshot_names(
    tmp_path,
    monkeypatch,
):
    first = tmp_path / "first" / "applications"
    second = tmp_path / "second" / "applications"
    _write_desktop(
        first / "duplicate.desktop",
        name="Middle First",
        extra="StartupWMClass=SharedAlias\n",
    )
    _write_desktop(
        first / "zulu.desktop",
        name="Zulu",
        extra="StartupWMClass=SharedAlias\n",
    )
    _write_desktop(
        second / "alpha.desktop",
        name="Alpha",
        extra="StartupWMClass=SharedAlias\n",
    )
    _write_desktop(
        second / "duplicate.desktop",
        name="Duplicate Lower",
        extra="StartupWMClass=SharedAlias\n",
    )
    original_rglob = Path.rglob

    def source_order(path: Path, pattern: str):
        if path == first:
            return iter((first / "duplicate.desktop", first / "zulu.desktop"))
        if path == second:
            return iter((second / "alpha.desktop", second / "duplicate.desktop"))
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", source_order)
    registry = _registry(directories=[first, second])

    registry.refresh()

    assert [application.name for application in registry.snapshot()] == [
        "Alpha",
        "Middle First",
        "Zulu",
    ]
    assert [
        application.desktop_id
        for application in registry.resolve_all_by_wm_class("sharedalias")
    ] == [
        "duplicate.desktop",
        "zulu.desktop",
        "alpha.desktop",
    ]
    assert registry.get("duplicate.desktop").name == "Middle First"


def test_alias_candidates_follow_raw_creation_order_within_one_directory(
    tmp_path,
    monkeypatch,
):
    applications = tmp_path / "applications"
    created_first = applications / "z-created-first.desktop"
    created_second = applications / "a-created-second.desktop"
    _write_desktop(
        created_first,
        name="Zulu Created First",
        extra="StartupWMClass=CreationCollision\n",
    )
    _write_desktop(
        created_second,
        name="Alpha Created Second",
        extra="StartupWMClass=CreationCollision\n",
    )
    original_rglob = Path.rglob

    def creation_order(path: Path, pattern: str):
        if path == applications:
            return iter((created_first, created_second))
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", creation_order)
    registry = _registry(directories=[applications])

    registry.refresh()

    assert [application.name for application in registry.snapshot()] == [
        "Alpha Created Second",
        "Zulu Created First",
    ]
    assert [
        application.desktop_id
        for application in registry.resolve_all_by_wm_class("creationcollision")
    ] == [
        "z-created-first.desktop",
        "a-created-second.desktop",
    ]


def test_gio_metadata_keeps_exact_file_and_action_provenance(tmp_path):
    first = tmp_path / "first" / "applications"
    second = tmp_path / "second" / "applications"
    desktop_id = "org.example.Actions.desktop"
    _write_desktop(
        first / desktop_id,
        name="First File",
        extra="Actions=first-only;\n\n[Desktop Action first-only]\nName=First Only\n",
    )
    _write_desktop(
        second / desktop_id,
        name="Second File",
        extra=(
            "Actions=shared;file-only;\n"
            "Categories=Development;\n"
            "\n"
            "[Desktop Action shared]\n"
            "Name=Shared from File\n"
            "Exec=actions --shared\n"
            "\n"
            "[Desktop Action file-only]\n"
            "Name=File Only\n"
            "Exec=actions --file\n"
        ),
    )
    app_info = _GioApplication(
        desktop_id,
        name="Actions from Gio",
        filename=str(second / desktop_id),
        icon="org.example.Actions",
        wm_class="Actions",
        commandline="actions %U",
        categories="Development;Utility;",
        description="Perform actions",
        keywords=("Actions", "Utility"),
        actions={
            "shared": "Shared from Gio",
            "gio-only": "Gio Only",
        },
    )
    registry = _registry(
        applications=[app_info],
        directories=[first, second],
    )

    assert registry.refresh() is True

    application = registry.get(desktop_id)
    assert application is not None
    assert application.desktop_file == second / desktop_id
    assert application.has_gio_source is True
    assert application.declared_icon == "org.example.Actions"
    assert application.categories == ("Development", "Utility")
    assert [action.action_id for action in application.actions] == [
        "shared",
        "gio-only",
        "file-only",
    ]
    shared, gio_only, file_only = application.actions
    assert shared.name == "Shared from Gio"
    assert shared.sources == frozenset({ActionSource.GIO, ActionSource.DESKTOP_FILE})
    assert shared.file_exec_line == "actions --shared"
    assert gio_only.sources == frozenset({ActionSource.GIO})
    assert file_only.sources == frozenset({ActionSource.DESKTOP_FILE})
    assert registry._gio_handle_for(desktop_id) is app_info


@pytest.mark.parametrize(
    ("gio_description", "file_metadata", "expected"),
    [
        (
            "Gio Description",
            "Comment=File Comment\nGenericName=File Generic\n",
            "Gio Description",
        ),
        (
            "",
            "Comment=File Comment\nGenericName=File Generic\n",
            "File Comment",
        ),
        ("", "GenericName=File Generic\n", "File Generic"),
        ("", "", ""),
    ],
)
def test_registry_preserves_legacy_search_description_fallback(
    tmp_path,
    gio_description,
    file_metadata,
    expected,
):
    apps = tmp_path / "applications"
    desktop_id = "description.desktop"
    path = apps / desktop_id
    _write_desktop(path, name="Description", extra=file_metadata)
    app_info = _GioApplication(
        desktop_id,
        name="Description",
        filename=str(path),
        commandline="description",
        generic_name="Gio Generic Must Not Leak Into Search",
        description=gio_description,
    )
    registry = _registry(applications=[app_info], directories=[apps])

    registry.refresh()

    application = registry.get(desktop_id)
    assert application is not None
    assert application.generic_name == "Gio Generic Must Not Leak Into Search"
    assert application.description == expected
    assert search_metadata(application).description == expected


def test_gio_hidden_and_no_display_are_distinct_from_resolvability():
    visible = _GioApplication(
        "visible.desktop",
        name="Visible",
        commandline="visible",
    )
    direct_only = _GioApplication(
        "direct-only.desktop",
        name="Direct Only",
        commandline="direct-only",
        no_display=True,
    )
    hidden = _GioApplication(
        "hidden.desktop",
        name="Hidden",
        commandline="hidden",
        hidden=True,
    )
    registry = _registry(applications=[visible, direct_only, hidden])

    registry.refresh()

    assert registry.get("visible.desktop") is not None
    assert registry.get("direct-only.desktop") is not None
    assert registry.get("hidden.desktop") is None
    assert [application.desktop_id for application in registry.snapshot()] == [
        "visible.desktop"
    ]


def test_generated_origin_and_host_location_are_independent(tmp_path, monkeypatch):
    host_apps = tmp_path / "host" / "applications"
    desktop_id = "docking-generated-tool-123.desktop"
    _write_desktop(
        host_apps / desktop_id,
        name="Generated Tool",
        extra="X-Docking-Generated=true\n",
    )
    monkeypatch.setattr(
        registry_mod.desktop_entries,
        "is_host_desktop_file",
        lambda path: path is not None and "host" in path.parts,
    )
    registry = _registry(directories=[host_apps])

    registry.refresh()

    application = registry.get(desktop_id)
    assert application is not None
    assert application.origin is ApplicationOrigin.GENERATED
    assert application.location is ApplicationLocation.HOST


def test_failed_refresh_rolls_back_every_published_value(tmp_path):
    apps = tmp_path / "applications"
    _write_desktop(apps / "stable.desktop", name="Stable")
    registry = _registry(directories=[apps])
    generations: list[int] = []
    registry.add_listener(lambda: generations.append(registry.generation))
    assert registry.refresh() is True
    published_snapshot = registry.snapshot()
    published_map = registry.applications_by_id
    generation = registry.generation

    def fail_discovery():
        raise RuntimeError("discovery failed")

    registry._application_source = fail_discovery

    assert registry.refresh() is False
    assert registry.snapshot() is published_snapshot
    assert registry.applications_by_id is published_map
    assert registry.generation == generation
    assert generations == [1]


def test_listener_subscription_is_unique_removable_and_idempotent(tmp_path):
    apps = tmp_path / "applications"
    _write_desktop(apps / "first.desktop", name="First")
    registry = _registry(directories=[apps])
    calls: list[int] = []

    def listener() -> None:
        calls.append(registry.generation)

    registry.add_listener(listener)
    registry.add_listener(listener)
    unsubscribe = registry.subscribe(listener)
    assert registry.refresh() is True
    unsubscribe()
    unsubscribe()
    _write_desktop(apps / "second.desktop", name="Second")
    assert registry.refresh() is True
    registry.remove_listener(listener)

    assert calls == [1]


def test_monitoring_debounces_and_rejects_stale_lifecycle_callbacks(tmp_path):
    apps = tmp_path / "applications"
    apps.mkdir()
    app_monitor = _SignalSource()
    directory_monitors: list[_DirectoryMonitor] = []
    scheduler = _Scheduler()
    registry = _registry(directories=[apps])
    registry._app_monitor_factory = lambda: app_monitor

    def monitor_directory(_path: Path) -> _DirectoryMonitor:
        monitor = _DirectoryMonitor()
        directory_monitors.append(monitor)
        return monitor

    registry._directory_monitor_factory = monitor_directory
    registry._schedule_timeout = scheduler.schedule
    registry._cancel_timeout = scheduler.cancel
    generations: list[int] = []
    registry.subscribe(lambda: generations.append(registry.generation))

    registry.start()
    registry.start()

    assert registry.started is True
    assert generations == [1]
    assert len(app_monitor.callbacks) == 1
    assert len(directory_monitors) == 1

    _write_desktop(apps / "new.desktop", name="New")
    app_monitor.emit_changed()
    directory_monitors[0].emit_changed()

    assert scheduler.delays == [DEFAULT_DEBOUNCE_MS]
    assert scheduler.run(1) is False
    assert generations == [1, 2]
    assert registry.get("new.desktop") is not None

    app_monitor.emit_changed()
    registry.stop()
    registry.stop()

    assert registry.started is False
    assert scheduler.cancelled == [2]
    assert app_monitor.disconnected == [1]
    assert directory_monitors[0].cancelled is True

    registry.start()
    generation = registry.generation
    app_monitor.emit_changed()
    assert registry._debounce_source_id == 3
    assert scheduler.run(2) is False
    assert registry._debounce_source_id == 3
    assert registry.generation == generation
    assert scheduler.run(3) is False
    registry.stop()


def test_monitor_set_reconciles_directory_changes(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    directories = [first]
    monitors: dict[Path, list[_DirectoryMonitor]] = {}
    registry = ApplicationRegistry(
        application_source=list,
        desktop_directories_source=lambda: directories,
    )
    registry._desktop_app_info_from_filename = lambda _path: None
    registry._app_monitor_factory = _SignalSource

    def monitor_directory(path: Path) -> _DirectoryMonitor:
        monitor = _DirectoryMonitor()
        monitors.setdefault(path, []).append(monitor)
        return monitor

    registry._directory_monitor_factory = monitor_directory
    registry.start()

    directories[:] = [second]
    registry.refresh()

    assert monitors[first][0].cancelled is True
    assert monitors[second][0].cancelled is False
    registry.stop()
    assert monitors[second][0].cancelled is True


def test_content_type_file_lookup_accepts_canonical_path_aliases(
    tmp_path,
    monkeypatch,
):
    apps = tmp_path / "applications"
    path = apps / "canonical.desktop"
    _write_desktop(path, name="Canonical")
    alias = tmp_path / "desktop-alias"
    alias.symlink_to(path)
    source = _GioApplication(
        "canonical.desktop",
        name="Canonical",
        filename=str(path),
        commandline="canonical",
    )
    registry = _registry(applications=[source], directories=[apps])
    registry.refresh()
    application = registry.get("canonical.desktop")
    assert application is not None
    assert application.desktop_file == path

    content_handler = _GioApplication(
        "",
        name="Canonical Through Alias",
        filename=str(alias),
    )
    monkeypatch.setattr(
        registry_mod.Gio.AppInfo,
        "get_default_for_type",
        lambda _content_type, _must_support_uris: content_handler,
    )

    assert registry.default_for_content_type("application/example") is application


def test_read_apis_use_snapshots_and_content_type_lookups_canonicalize(
    monkeypatch,
):
    visible = _GioApplication(
        "visible.desktop",
        name="Visible",
        commandline="visible",
        wm_class="Shared",
    )
    direct_only = _GioApplication(
        "direct.desktop",
        name="Direct",
        commandline="direct",
        no_display=True,
    )
    registry = _registry(applications=[visible, direct_only])
    registry.refresh()

    registry._application_source = lambda: (_ for _ in ()).throw(
        AssertionError("read path rediscovered applications")
    )
    assert registry.get("visible.desktop") is not None
    assert registry.resolve("visible.desktop") is not None
    assert registry.resolve_all_by_wm_class("shared")[0].desktop_id == (
        "visible.desktop"
    )
    assert registry.snapshot()[0].desktop_id == "visible.desktop"

    default_calls: list[tuple[str, bool]] = []

    def get_default(content_type: str, must_support_uris: bool):
        default_calls.append((content_type, must_support_uris))
        return direct_only

    monkeypatch.setattr(
        registry_mod.Gio.AppInfo,
        "get_default_for_type",
        get_default,
    )
    monkeypatch.setattr(
        registry_mod.Gio.AppInfo,
        "get_recommended_for_type",
        lambda _content_type: [visible, direct_only, visible],
    )

    assert registry.default_for_content_type("audio/mpeg") is registry.get(
        "direct.desktop"
    )
    assert registry.recommended_for_content_type("audio/mpeg") == (
        registry.get("visible.desktop"),
    )
    assert default_calls == [("audio/mpeg", False)]


@pytest.mark.parametrize(
    "desktop_id",
    ["", "org.example.Unregistered.desktop"],
    ids=["idless", "identified-unregistered"],
)
def test_launchable_default_retains_unregistered_handler_metadata_and_handle(
    monkeypatch,
    desktop_id,
):
    registry = _registry()
    registry.refresh()
    handler = _GioApplication(
        desktop_id,
        name="External Handler",
        commandline="external-handler %U",
        icon="external-handler",
        categories="AudioVideo;",
        description="Handles external media",
        generic_name="Media Handler",
    )
    monkeypatch.setattr(
        registry_mod.Gio.AppInfo,
        "get_default_for_type",
        lambda _content_type, _must_support_uris: handler,
    )

    listing = registry.default_listing_for_content_type("application/example")

    assert isinstance(listing, UnidentifiedApplicationListing)
    assert listing.name == "External Handler"
    assert listing.exec_line == "external-handler %U"
    assert listing.description == "Handles external media"
    assert listing.generic_name == "Media Handler"
    assert registry._gio_handle_for_unidentified(listing.listing_key) is handler
    assert registry.get(desktop_id) is None


def test_launchable_recommended_handlers_include_registered_idless_and_unregistered(
    monkeypatch,
):
    registered_handler = _GioApplication(
        "registered.desktop",
        name="Registered",
        commandline="registered",
    )
    registry = _registry(applications=[registered_handler])
    registry.refresh()
    registered = registry.get("registered.desktop")
    assert registered is not None

    idless = _GioApplication(
        "",
        name="ID-less Recommended",
        commandline="idless",
    )
    unregistered = _GioApplication(
        "unregistered.desktop",
        name="Unregistered Recommended",
        commandline="unregistered",
    )
    monkeypatch.setattr(
        registry_mod.Gio.AppInfo,
        "get_recommended_for_type",
        lambda _content_type: [
            registered_handler,
            idless,
            unregistered,
            unregistered,
        ],
    )

    listings = registry.recommended_listings_for_content_type("application/example")

    assert listings[0] is registered
    assert [
        listing.name
        for listing in listings[1:]
        if isinstance(listing, UnidentifiedApplicationListing)
    ] == ["ID-less Recommended", "Unregistered Recommended"]
    assert all(
        registry._gio_handle_for_unidentified(listing.listing_key)
        in {idless, unregistered}
        for listing in listings[1:]
        if isinstance(listing, UnidentifiedApplicationListing)
    )


def test_content_handler_tokens_are_bounded_and_expire_on_refresh(monkeypatch):
    registry = _registry()
    registry.refresh()
    handlers = [
        _GioApplication("", name=f"Handler {index}", commandline=f"handler-{index}")
        for index in range(3)
    ]
    selected = iter(handlers)
    monkeypatch.setattr(registry_mod, "MAX_CONTENT_HANDLER_TOKENS", 2)
    monkeypatch.setattr(
        registry_mod.Gio.AppInfo,
        "get_default_for_type",
        lambda _content_type, _must_support_uris: next(selected),
    )

    listings = [
        registry.default_listing_for_content_type("application/example")
        for _handler in handlers
    ]
    assert all(
        isinstance(listing, UnidentifiedApplicationListing) for listing in listings
    )
    first, second, third = listings
    assert isinstance(first, UnidentifiedApplicationListing)
    assert isinstance(second, UnidentifiedApplicationListing)
    assert isinstance(third, UnidentifiedApplicationListing)
    assert registry._gio_handle_for_unidentified(first.listing_key) is None
    assert registry._gio_handle_for_unidentified(second.listing_key) is handlers[1]
    assert registry._gio_handle_for_unidentified(third.listing_key) is handlers[2]

    assert registry.refresh() is False
    assert registry._gio_handle_for_unidentified(second.listing_key) is None
    assert registry._gio_handle_for_unidentified(third.listing_key) is None


def test_content_type_lookup_can_project_an_unlisted_gio_handler(monkeypatch):
    registry = _registry()
    registry.refresh()
    handler = _GioApplication(
        "unlisted.desktop",
        name="Unlisted",
        commandline="unlisted",
        icon="unlisted",
    )
    monkeypatch.setattr(
        registry_mod.Gio.AppInfo,
        "get_default_for_type",
        lambda _content_type, _must_support_uris: handler,
    )

    application = registry.default_for_content_type("application/example")

    assert application is not None
    assert application.desktop_id == "unlisted.desktop"
    assert application.has_gio_source is True
    assert registry.get("unlisted.desktop") is None


def test_owner_thread_contract_rejects_mutating_gio_operations_from_workers():
    application = _GioApplication(
        "worker-readable.desktop",
        name="Worker Readable",
        commandline="worker-readable",
        wm_class="WorkerReadable",
    )
    registry = _registry(applications=[application])
    listener_threads: list[int] = []
    registry.add_listener(lambda: listener_threads.append(get_ident()))
    registry.refresh()
    owner_thread = get_ident()

    operations: tuple[tuple[str, Callable[[], object]], ...] = (
        ("refresh", registry.refresh),
        ("start", registry.start),
        ("stop", registry.stop),
        (
            "default_for_content_type",
            lambda: registry.default_for_content_type("application/example"),
        ),
        (
            "recommended_for_content_type",
            lambda: registry.recommended_for_content_type("application/example"),
        ),
        (
            "default_listing_for_content_type",
            lambda: registry.default_listing_for_content_type("application/example"),
        ),
        (
            "recommended_listings_for_content_type",
            lambda: registry.recommended_listings_for_content_type(
                "application/example"
            ),
        ),
        (
            "_gio_handle_for",
            lambda: registry._gio_handle_for("worker-readable.desktop"),
        ),
        (
            "_gio_handle_for_unidentified",
            lambda: registry._gio_handle_for_unidentified("missing"),
        ),
    )

    errors: list[tuple[str, Exception]] = []
    for name, operation in operations:

        def run(
            operation: Callable[[], object] = operation,
            name: str = name,
        ) -> None:
            try:
                operation()
            except Exception as exc:
                errors.append((name, exc))

        thread = Thread(target=run)
        thread.start()
        thread.join()

    assert [name for name, _error in errors] == [
        name for name, _operation in operations
    ]
    assert all(isinstance(error, RuntimeError) for _name, error in errors)
    assert all("owner thread" in str(error) for _name, error in errors)
    assert registry.started is False
    assert listener_threads == [owner_thread]


def test_snapshot_and_resolver_reads_remain_available_from_worker_threads():
    application = _GioApplication(
        "worker-readable.desktop",
        name="Worker Readable",
        commandline="worker-readable",
        wm_class="WorkerReadable",
    )
    registry = _registry(applications=[application])
    registry.refresh()
    outcomes: list[object] = []
    errors: list[Exception] = []

    def read_snapshots() -> None:
        try:
            outcomes.extend(
                (
                    registry.snapshot(),
                    registry.resolvable_snapshot(),
                    registry.unidentified_snapshot(),
                    registry.get("worker-readable.desktop"),
                    registry.resolve("worker-readable.desktop"),
                    registry.resolve_by_wm_class("workerreadable"),
                    registry.resolve_all_by_executable_path(Path("/missing")),
                    registry.resolve_by_desktop_file(Path("/missing.desktop")),
                )
            )
        except Exception as exc:
            errors.append(exc)

    thread = Thread(target=read_snapshots)
    thread.start()
    thread.join()

    assert errors == []
    assert outcomes[0] == registry.snapshot()
    assert outcomes[1] == registry.resolvable_snapshot()
    assert outcomes[2] == ()
    assert outcomes[3] is registry.get("worker-readable.desktop")
    assert outcomes[4] is registry.get("worker-readable.desktop")
    assert outcomes[5] is registry.get("worker-readable.desktop")
    assert outcomes[6] == ()
    assert outcomes[7] is None
