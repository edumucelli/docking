"""Tests for the Global Search desktop application catalog."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import docking.search.services.application_catalog as catalog_mod
from docking.search.services.application_catalog import (
    ApplicationCatalog,
    DesktopActionSnapshot,
    IconDescriptor,
)


class _FakeAppInfo:
    def __init__(
        self,
        *,
        description: str = "",
        keywords: tuple[str, ...] = (),
        actions: dict[str, str] | None = None,
        filename: str = "",
        icon: object | None = None,
    ) -> None:
        self.description = description
        self.keywords = keywords
        self.actions = actions or {}
        self.filename = filename
        self.icon = icon

    def get_description(self) -> str:
        return self.description

    def get_keywords(self) -> tuple[str, ...]:
        return self.keywords

    def list_actions(self) -> tuple[str, ...]:
        return tuple(self.actions)

    def get_action_name(self, action_id: str) -> str:
        return self.actions[action_id]

    def get_filename(self) -> str:
        return self.filename

    def get_icon(self) -> object | None:
        return self.icon


class _FakeApplicationEntry:
    def __init__(
        self,
        desktop_id: str,
        name: str,
        *,
        categories: str = "",
        icon_name: str = "",
        app_info: object | None = None,
    ) -> None:
        self.desktop_id = desktop_id
        self.name = name
        self.categories = categories
        self.icon_name = icon_name
        self.app_info = app_info

    def get_id(self) -> str:
        return self.desktop_id

    def get_display_name(self) -> str:
        return self.name

    def get_categories(self) -> str:
        return self.categories


class _FakeSignalSource:
    def __init__(self) -> None:
        self.callbacks: dict[int, Callable[..., object]] = {}
        self.disconnected: list[int] = []

    def connect(self, signal: str, callback: Callable[..., object]) -> int:
        assert signal == "changed"
        handler_id = len(self.callbacks) + 1
        self.callbacks[handler_id] = callback
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        self.disconnected.append(handler_id)
        self.callbacks.pop(handler_id, None)

    def emit_changed(self) -> None:
        for callback in tuple(self.callbacks.values()):
            callback(self)


class _FakeDirectoryMonitor(_FakeSignalSource):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _FakeScheduler:
    def __init__(self) -> None:
        self.callbacks: dict[int, Callable[[], bool]] = {}
        self.cancelled: list[int] = []
        self.delays: list[int] = []

    def schedule(self, delay: int, callback: Callable[[], bool]) -> int:
        source_id = len(self.callbacks) + 1
        self.delays.append(delay)
        self.callbacks[source_id] = callback
        return source_id

    def cancel(self, source_id: int) -> None:
        self.cancelled.append(source_id)

    def run(self, source_id: int) -> bool:
        callback = self.callbacks[source_id]
        return callback()


def test_builds_normalized_plain_immutable_snapshots(monkeypatch):
    monkeypatch.setattr(
        catalog_mod.desktop_entries,
        "find_desktop_file",
        lambda _desktop_id: None,
    )
    app_info = _FakeAppInfo(
        description="  Write and   edit documents. ",
        keywords=("Write", " Documents ", "write"),
        actions={"new-window": " New Window "},
    )
    entry = _FakeApplicationEntry(
        "org.example.Writer.desktop",
        "  Café   Writer ",
        categories=" Office ;Utility;office;;",
        icon_name="/opt/example/writer.svg",
        app_info=app_info,
    )
    changed: list[int] = []
    catalog = ApplicationCatalog(
        application_source=lambda: [entry, entry],
        desktop_directories_source=list,
    )
    catalog.add_listener(lambda: changed.append(catalog.generation))

    assert catalog.refresh() is True
    assert catalog.refresh() is False

    assert changed == [1]
    assert catalog.generation == 1
    assert len(catalog.snapshot()) == 1
    snapshot = catalog.get("org.example.Writer.desktop")
    assert snapshot is not None
    assert snapshot.name == "Café Writer"
    assert snapshot.normalized_name == "café writer"
    assert snapshot.categories == ("Office", "Utility")
    assert snapshot.icon == IconDescriptor(
        kind="file",
        value="/opt/example/writer.svg",
    )
    assert snapshot.icon_descriptor is snapshot.icon
    assert snapshot.description == "Write and edit documents."
    assert snapshot.keywords == ("Write", "Documents")
    assert snapshot.actions == (
        DesktopActionSnapshot(action_id="new-window", name="New Window"),
    )

    with pytest.raises(FrozenInstanceError):
        snapshot.name = "Changed"  # ty: ignore[invalid-assignment]


def test_uses_desktop_file_metadata_as_best_effort_fallback(tmp_path, monkeypatch):
    desktop_file = tmp_path / "org.example.Tool.desktop"
    desktop_file.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Example Tool\n"
        "Comment=Inspect useful things\n"
        "Keywords=Inspect;Utility;\n"
        "Actions=new-window;private;\n"
        "\n"
        "[Desktop Action new-window]\n"
        "Name=New Window\n"
        "\n"
        "[Desktop Action private]\n"
        "Name=Private Session\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        catalog_mod.desktop_entries,
        "find_desktop_file",
        lambda _desktop_id: desktop_file,
    )
    entry = _FakeApplicationEntry(
        "org.example.Tool.desktop",
        "Example Tool",
        categories="Development;",
        icon_name="org.example.Tool",
    )
    catalog = ApplicationCatalog(
        application_source=lambda: [entry],
        desktop_directories_source=list,
    )

    catalog.refresh()

    snapshot = catalog.snapshot()[0]
    assert snapshot.description == "Inspect useful things"
    assert snapshot.keywords == ("Inspect", "Utility")
    assert snapshot.actions == (
        DesktopActionSnapshot("new-window", "New Window"),
        DesktopActionSnapshot("private", "Private Session"),
    )
    assert snapshot.icon == IconDescriptor("themed", "org.example.Tool")


def test_start_stop_and_monitor_refresh_are_idempotent(monkeypatch):
    monkeypatch.setattr(
        catalog_mod.desktop_entries,
        "find_desktop_file",
        lambda _desktop_id: None,
    )
    applications: list[_FakeApplicationEntry] = []
    app_monitor = _FakeSignalSource()
    scheduler = _FakeScheduler()
    directory_monitors: dict[Path, _FakeDirectoryMonitor] = {}
    directories = [Path("/apps/one"), Path("/apps/two")]
    source_calls = 0

    def application_source():
        nonlocal source_calls
        source_calls += 1
        return list(applications)

    def monitor_directory(path: Path) -> _FakeDirectoryMonitor:
        monitor = _FakeDirectoryMonitor()
        directory_monitors[path] = monitor
        return monitor

    catalog = ApplicationCatalog(
        application_source=application_source,
        desktop_directories_source=lambda: directories,
        app_monitor_factory=lambda: app_monitor,
        directory_monitor_factory=monitor_directory,
        schedule_timeout=scheduler.schedule,
        cancel_timeout=scheduler.cancel,
        debounce_ms=75,
    )
    generations: list[int] = []
    unsubscribe = catalog.subscribe(
        lambda: generations.append(catalog.generation),
    )

    catalog.start()
    catalog.start()

    assert catalog.started is True
    assert source_calls == 1
    assert generations == [1]
    assert len(app_monitor.callbacks) == 1
    assert set(directory_monitors) == set(directories)

    applications.append(
        _FakeApplicationEntry(
            "org.example.New.desktop",
            "New App",
            icon_name="application-x-executable",
        )
    )
    app_monitor.emit_changed()
    directory_monitors[directories[0]].emit_changed()

    assert source_calls == 1
    assert scheduler.delays == [75]
    assert scheduler.run(1) is False
    assert source_calls == 2
    assert generations == [1, 2]

    app_monitor.emit_changed()
    catalog.stop()
    catalog.stop()

    assert catalog.started is False
    assert scheduler.cancelled == [2]
    assert app_monitor.disconnected == [1]
    assert all(monitor.cancelled for monitor in directory_monitors.values())
    assert catalog.snapshot()[0].desktop_id == "org.example.New.desktop"

    unsubscribe()
    catalog.start()
    assert generations == [1, 2]


def test_directory_monitor_set_is_reconciled(monkeypatch):
    monkeypatch.setattr(
        catalog_mod.desktop_entries,
        "find_desktop_file",
        lambda _desktop_id: None,
    )
    app_monitor = _FakeSignalSource()
    first = Path("/apps/first")
    second = Path("/apps/second")
    directories = [first]
    monitors: dict[Path, list[_FakeDirectoryMonitor]] = {}

    def monitor_directory(path: Path) -> _FakeDirectoryMonitor:
        monitor = _FakeDirectoryMonitor()
        monitors.setdefault(path, []).append(monitor)
        return monitor

    catalog = ApplicationCatalog(
        application_source=list,
        desktop_directories_source=lambda: directories,
        app_monitor_factory=lambda: app_monitor,
        directory_monitor_factory=monitor_directory,
    )
    catalog.start()

    directories[:] = [second]
    catalog.refresh()

    assert monitors[first][0].cancelled is True
    assert monitors[second][0].cancelled is False
    catalog.stop()
    assert monitors[second][0].cancelled is True
