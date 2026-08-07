"""Tests for the Global Search recent-files catalog."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, call

import pytest

from docking.search.services.recent_files import (
    RecentFilesCatalog,
    RecentFileSnapshot,
)


class _FakeRecentItem:
    def __init__(
        self,
        name: str,
        uri: str,
        modified: int,
        *,
        exists: bool = True,
        mime_type: str = "",
    ) -> None:
        self.name = name
        self.uri = uri
        self.modified = modified
        self.is_present = exists
        self.mime_type = mime_type

    def exists(self) -> bool:
        return self.is_present

    def get_display_name(self) -> str:
        return self.name

    def get_short_name(self) -> str:
        return self.name

    def get_uri(self) -> str:
        return self.uri

    def get_modified(self) -> int:
        return self.modified

    def get_mime_type(self) -> str:
        return self.mime_type


class _FakeRecentManager:
    def __init__(self, items: list[object] | None = None) -> None:
        self.items = items or []
        self.callbacks: dict[int, Callable[..., object]] = {}
        self.disconnected: list[int] = []
        self.get_items_calls = 0
        self.purge_calls = 0

    def get_items(self) -> list[object]:
        self.get_items_calls += 1
        return list(self.items)

    def connect(self, signal: str, callback: Callable[..., object]) -> int:
        assert signal == "changed"
        handler_id = len(self.callbacks) + 1
        self.callbacks[handler_id] = callback
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        self.disconnected.append(handler_id)
        self.callbacks.pop(handler_id, None)

    def purge_items(self) -> int:
        self.purge_calls += 1
        count = len(self.items)
        self.items = []
        return count

    def emit_changed(self) -> None:
        for callback in tuple(self.callbacks.values()):
            callback(self)


def test_lists_existing_files_most_recent_first_as_frozen_snapshots():
    manager = _FakeRecentManager(
        [
            _FakeRecentItem(
                "old.txt",
                "file:///old.txt",
                10,
                mime_type="text/plain",
            ),
            _FakeRecentItem(
                "gone.txt",
                "file:///gone.txt",
                100,
                exists=False,
            ),
            _FakeRecentItem(
                "new.pdf",
                "file:///new.pdf",
                30,
                mime_type="application/pdf",
            ),
            _FakeRecentItem(
                "middle.txt",
                "file:///middle.txt",
                20,
                mime_type="text/plain",
            ),
        ]
    )
    catalog = RecentFilesCatalog()
    catalog._manager_factory = lambda: manager
    catalog._max_entries = 2
    generations: list[int] = []
    catalog.add_listener(lambda: generations.append(catalog.generation))

    catalog.start()
    catalog.start()

    assert manager.get_items_calls == 1
    assert generations == [1]
    assert catalog.generation == 1
    assert catalog.entries == catalog.snapshot()
    assert catalog.snapshot() == (
        RecentFileSnapshot(
            name="new.pdf",
            uri="file:///new.pdf",
            modified=30,
            mime_type="application/pdf",
        ),
        RecentFileSnapshot(
            name="middle.txt",
            uri="file:///middle.txt",
            modified=20,
            mime_type="text/plain",
        ),
    )
    assert catalog.snapshot()[0].display_name == "new.pdf"

    with pytest.raises(FrozenInstanceError):
        catalog.snapshot()[0].name = "changed"  # ty: ignore[invalid-assignment]


def test_manager_changes_advance_generation_only_for_new_data():
    manager = _FakeRecentManager()
    catalog = RecentFilesCatalog()
    catalog._manager_factory = lambda: manager
    notifications: list[int] = []
    unsubscribe = catalog.subscribe(
        lambda: notifications.append(catalog.generation),
    )

    catalog.start()
    manager.emit_changed()
    assert notifications == [1]

    manager.items.append(_FakeRecentItem("notes.txt", "file:///notes.txt", 42))
    manager.emit_changed()
    manager.emit_changed()

    assert notifications == [1, 2]
    assert catalog.snapshot()[0].name == "notes.txt"

    catalog.stop()
    catalog.stop()
    assert manager.disconnected == [1]
    assert catalog.started is False

    manager.items.append(_FakeRecentItem("new.txt", "file:///new.txt", 100))
    manager.emit_changed()
    assert notifications == [1, 2]

    unsubscribe()
    catalog.start()
    assert catalog.generation == 3
    assert notifications == [1, 2]
    catalog.stop()


def test_open_and_clear_helpers_update_the_catalog():
    manager = _FakeRecentManager(
        [_FakeRecentItem("notes.txt", "file:///notes.txt", 42)]
    )
    launched: list[str] = []
    catalog = RecentFilesCatalog()
    catalog._manager_factory = lambda: manager
    catalog._uri_launcher = launched.append
    catalog.start()
    entry = catalog.snapshot()[0]

    assert catalog.open(entry) is True
    assert catalog.open_uri(" file:///other.txt ") is True
    assert catalog.open_uri("") is False
    assert launched == ["file:///notes.txt", "file:///other.txt"]

    assert catalog.clear() is True
    assert manager.purge_calls == 1
    assert catalog.snapshot() == ()
    assert catalog.generation == 2


def test_open_and_clear_failures_are_contained():
    class _FailingManager(_FakeRecentManager):
        def purge_items(self) -> int:
            raise RuntimeError("purge failed")

    manager = _FailingManager()

    def fail_to_launch(_uri: str) -> None:
        raise RuntimeError("launch failed")

    catalog = RecentFilesCatalog()
    catalog._manager_factory = lambda: manager
    catalog._uri_launcher = fail_to_launch

    assert catalog.open_uri("file:///notes.txt") is False
    assert catalog.clear_recent() is False


def test_injected_target_service_controls_exact_open_result():
    target_service = MagicMock()
    target_service.open_target.side_effect = [True, False]
    catalog = RecentFilesCatalog(target_service=target_service)

    assert catalog.open_uri(" file:///opened.txt ") is True
    assert catalog.open_uri("file:///rejected.txt") is False
    assert target_service.open_target.call_args_list == [
        call("file:///opened.txt"),
        call("file:///rejected.txt"),
    ]


def test_skips_invalid_items_deduplicates_uris_and_falls_back_to_uri_name():
    class _BrokenItem:
        def exists(self) -> bool:
            raise ValueError("invalid")

    first = _FakeRecentItem("", "file:///tmp/report.txt", 20)
    duplicate = _FakeRecentItem(
        "duplicate.txt",
        "file:///tmp/report.txt",
        10,
    )
    manager = _FakeRecentManager([_BrokenItem(), duplicate, first])
    catalog = RecentFilesCatalog()
    catalog._manager_factory = lambda: manager

    catalog.refresh()

    assert catalog.snapshot() == (
        RecentFileSnapshot(
            name="report.txt",
            uri="file:///tmp/report.txt",
            modified=20,
        ),
    )
