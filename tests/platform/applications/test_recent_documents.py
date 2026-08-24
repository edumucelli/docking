"""Tests for canonical recent-document association."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from unittest.mock import MagicMock

import pytest

import docking.platform.applications.recent_documents as recent_documents_mod
from docking.platform.applications.recent_documents import (
    RecentDocument,
    recent_documents_for,
)
from tests.platform.application_fakes import application


def _application(name: str):
    return replace(application("writer.desktop"), name=name)


class _RecentItem:
    def __init__(
        self,
        uri: str,
        *,
        modified: int,
        local: bool = True,
        exists: bool = True,
        mime_type: str = "text/plain",
        associated: bool = True,
    ) -> None:
        self.uri = uri
        self.modified = modified
        self.local = local
        self.present = exists
        self.mime_type = mime_type
        self.associated = associated
        self.application_names: list[str] = []

    def get_uri(self) -> str:
        return self.uri

    def get_short_name(self) -> str:
        return self.uri.rsplit("/", 1)[-1]

    def get_mime_type(self) -> str:
        return self.mime_type

    def get_modified(self) -> int:
        return self.modified

    def is_local(self) -> bool:
        return self.local

    def exists(self) -> bool:
        return self.present

    def has_application(self, name: str) -> bool:
        self.application_names.append(name)
        return self.associated


def _manager(monkeypatch, items: list[_RecentItem]) -> MagicMock:
    manager = MagicMock()
    manager.get_items.return_value = items
    monkeypatch.setattr(
        recent_documents_mod.Gtk.RecentManager,
        "get_default",
        lambda: manager,
    )
    return manager


def test_recent_document_is_an_immutable_value() -> None:
    document = RecentDocument(
        uri="file:///tmp/example.txt",
        name="example.txt",
        mime_type="text/plain",
        modified=42,
    )

    with pytest.raises(FrozenInstanceError):
        document.uri = "file:///tmp/changed.txt"  # ty: ignore[invalid-assignment]


def test_empty_store_returns_an_empty_snapshot(monkeypatch) -> None:
    manager = _manager(monkeypatch, [])

    assert recent_documents_for(_application("Writer"), limit=10) == ()
    manager.get_items.assert_called_once_with()


@pytest.mark.parametrize("limit", (0, -1))
def test_non_positive_limit_avoids_recent_store_lookup(monkeypatch, limit: int) -> None:
    get_default = MagicMock()
    monkeypatch.setattr(
        recent_documents_mod.Gtk.RecentManager,
        "get_default",
        get_default,
    )

    assert recent_documents_for(_application("Writer"), limit=limit) == ()
    get_default.assert_not_called()


def test_filters_invalid_or_unassociated_items(monkeypatch) -> None:
    kept = _RecentItem("file:///tmp/kept.txt", modified=1)
    items = [
        _RecentItem("file:///tmp/remote.txt", modified=5, local=False),
        _RecentItem("file:///tmp/missing.txt", modified=4, exists=False),
        _RecentItem("file:///tmp/no-mime.txt", modified=3, mime_type=""),
        _RecentItem("file:///tmp/other.txt", modified=2, associated=False),
        kept,
    ]
    _manager(monkeypatch, items)

    assert recent_documents_for(_application("Writer"), limit=10) == (
        RecentDocument(
            uri="file:///tmp/kept.txt",
            name="kept.txt",
            mime_type="text/plain",
            modified=1,
        ),
    )
    assert kept.application_names == ["Writer"]


def test_sorts_newest_first_and_applies_limit(monkeypatch) -> None:
    _manager(
        monkeypatch,
        [
            _RecentItem("file:///tmp/old.txt", modified=1),
            _RecentItem("file:///tmp/new.txt", modified=3),
            _RecentItem("file:///tmp/middle.txt", modified=2),
        ],
    )

    documents = recent_documents_for(_application("Writer"), limit=2)

    assert [document.name for document in documents] == ["new.txt", "middle.txt"]


def test_uses_exact_canonical_application_name(monkeypatch) -> None:
    item = _RecentItem("file:///tmp/example.odt", modified=4)
    _manager(monkeypatch, [item])

    recent_documents_for(_application("Canonical Writer"), limit=1)

    assert item.application_names == ["Canonical Writer"]
