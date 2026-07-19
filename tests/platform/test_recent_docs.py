"""Tests for recent_docs module - document-to-app association."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.recent_docs import RecentDoc, recent_docs_for_app


class _FakeRecentItem:
    """Minimal fake for Gtk.RecentInfo."""

    def __init__(
        self,
        uri: str,
        name: str,
        mime: str,
        modified: int,
        *,
        is_local: bool = True,
        exists: bool = True,
        has_application: bool = True,
    ):
        self._uri = uri
        self._name = name
        self._mime = mime
        self._modified = modified
        self._is_local = is_local
        self._exists = exists
        self._has_application = has_application

    def get_uri(self) -> str:
        return self._uri

    def get_short_name(self) -> str:
        return self._name

    def get_mime_type(self) -> str:
        return self._mime

    def get_modified(self) -> int:
        return self._modified

    def is_local(self) -> bool:
        return self._is_local

    def exists(self) -> bool:
        return self._exists

    def has_application(self, app_name: str) -> bool:
        return self._has_application


class TestRecentDocDataclass:
    def test_recent_doc_fields(self):
        doc = RecentDoc(
            uri="file:///home/user/doc.txt",
            name="doc.txt",
            mime_type="text/plain",
            modified=1000,
        )
        assert doc.uri == "file:///home/user/doc.txt"
        assert doc.name == "doc.txt"
        assert doc.mime_type == "text/plain"
        assert doc.modified == 1000

    def test_recent_doc_is_frozen(self):
        doc = RecentDoc(
            uri="file:///a",
            name="a",
            mime_type="text/plain",
            modified=1,
        )
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            doc.uri = "other"


class TestRecentDocsForApp:
    def test_empty_desktop_id_returns_empty(self):
        result = recent_docs_for_app("", launcher=MagicMock(), limit=10)
        assert result == []

    def test_nonexistent_desktop_entry_returns_empty(self, monkeypatch):
        import docking.platform.recent_docs as mod

        monkeypatch.setattr(
            mod.desktop_entries,
            "resolve_app_info",
            lambda desktop_id, **kw: None,
        )
        result = recent_docs_for_app("missing.desktop", launcher=MagicMock(), limit=10)
        assert result == []

    def test_empty_recent_store_returns_empty(self, monkeypatch):
        import docking.platform.recent_docs as mod

        monkeypatch.setattr(
            mod.desktop_entries,
            "resolve_app_info",
            lambda desktop_id, **kw: SimpleNamespace(
                app_info=SimpleNamespace(get_display_name=lambda: "Firefox")
            ),
        )
        fake_manager = MagicMock()
        fake_manager.get_items.return_value = []
        monkeypatch.setattr(mod.Gtk.RecentManager, "get_default", lambda: fake_manager)

        result = recent_docs_for_app("firefox.desktop", launcher=MagicMock(), limit=10)
        assert result == []

    def test_filters_non_local_files(self, monkeypatch):
        import docking.platform.recent_docs as mod

        monkeypatch.setattr(
            mod.desktop_entries,
            "resolve_app_info",
            lambda desktop_id, **kw: SimpleNamespace(
                app_info=SimpleNamespace(get_display_name=lambda: "Firefox")
            ),
        )
        items = [
            _FakeRecentItem(
                "file:///remote/doc.txt",
                "doc.txt",
                "text/plain",
                2000,
                is_local=False,
                exists=True,
                has_application=True,
            ),
            _FakeRecentItem(
                "file:///local/doc.txt",
                "doc.txt",
                "text/plain",
                1000,
                is_local=True,
                exists=True,
                has_application=True,
            ),
        ]
        fake_manager = MagicMock()
        fake_manager.get_items.return_value = items
        monkeypatch.setattr(mod.Gtk.RecentManager, "get_default", lambda: fake_manager)

        result = recent_docs_for_app("firefox.desktop", launcher=MagicMock(), limit=10)
        assert len(result) == 1
        assert result[0].uri == "file:///local/doc.txt"

    def test_filters_nonexistent_files(self, monkeypatch):
        import docking.platform.recent_docs as mod

        monkeypatch.setattr(
            mod.desktop_entries,
            "resolve_app_info",
            lambda desktop_id, **kw: SimpleNamespace(
                app_info=SimpleNamespace(get_display_name=lambda: "Firefox")
            ),
        )
        items = [
            _FakeRecentItem(
                "file:///deleted/doc.txt",
                "doc.txt",
                "text/plain",
                1000,
                is_local=True,
                exists=False,
                has_application=True,
            ),
            _FakeRecentItem(
                "file:///alive/doc.txt",
                "doc.txt",
                "text/plain",
                500,
                is_local=True,
                exists=True,
                has_application=True,
            ),
        ]
        fake_manager = MagicMock()
        fake_manager.get_items.return_value = items
        monkeypatch.setattr(mod.Gtk.RecentManager, "get_default", lambda: fake_manager)

        result = recent_docs_for_app("firefox.desktop", launcher=MagicMock(), limit=10)
        assert len(result) == 1
        assert result[0].uri == "file:///alive/doc.txt"

    def test_filters_wrong_application(self, monkeypatch):
        import docking.platform.recent_docs as mod

        monkeypatch.setattr(
            mod.desktop_entries,
            "resolve_app_info",
            lambda desktop_id, **kw: SimpleNamespace(
                app_info=SimpleNamespace(get_display_name=lambda: "Firefox")
            ),
        )
        items = [
            _FakeRecentItem(
                "file:///chrome/doc.txt",
                "doc.txt",
                "text/plain",
                1000,
                is_local=True,
                exists=True,
                has_application=False,
            ),
            _FakeRecentItem(
                "file:///firefox/doc.txt",
                "doc.txt",
                "text/plain",
                500,
                is_local=True,
                exists=True,
                has_application=True,
            ),
        ]
        fake_manager = MagicMock()
        fake_manager.get_items.return_value = items
        monkeypatch.setattr(mod.Gtk.RecentManager, "get_default", lambda: fake_manager)

        result = recent_docs_for_app("firefox.desktop", launcher=MagicMock(), limit=10)
        assert len(result) == 1
        assert result[0].uri == "file:///firefox/doc.txt"

    def test_respects_limit(self, monkeypatch):
        import docking.platform.recent_docs as mod

        monkeypatch.setattr(
            mod.desktop_entries,
            "resolve_app_info",
            lambda desktop_id, **kw: SimpleNamespace(
                app_info=SimpleNamespace(get_display_name=lambda: "Firefox")
            ),
        )
        items = [
            _FakeRecentItem(
                f"file:///doc{i}.txt",
                f"doc{i}.txt",
                "text/plain",
                1000 - i,
                is_local=True,
                exists=True,
                has_application=True,
            )
            for i in range(20)
        ]
        fake_manager = MagicMock()
        fake_manager.get_items.return_value = items
        monkeypatch.setattr(mod.Gtk.RecentManager, "get_default", lambda: fake_manager)

        result = recent_docs_for_app("firefox.desktop", launcher=MagicMock(), limit=5)
        assert len(result) == 5

    def test_sorts_by_modified_descending(self, monkeypatch):
        import docking.platform.recent_docs as mod

        monkeypatch.setattr(
            mod.desktop_entries,
            "resolve_app_info",
            lambda desktop_id, **kw: SimpleNamespace(
                app_info=SimpleNamespace(get_display_name=lambda: "Firefox")
            ),
        )
        items = [
            _FakeRecentItem(
                "file:///old.txt",
                "old.txt",
                "text/plain",
                500,
                is_local=True,
                exists=True,
                has_application=True,
            ),
            _FakeRecentItem(
                "file:///new.txt",
                "new.txt",
                "text/plain",
                2000,
                is_local=True,
                exists=True,
                has_application=True,
            ),
            _FakeRecentItem(
                "file:///mid.txt",
                "mid.txt",
                "text/plain",
                1000,
                is_local=True,
                exists=True,
                has_application=True,
            ),
        ]
        fake_manager = MagicMock()
        fake_manager.get_items.return_value = items
        monkeypatch.setattr(mod.Gtk.RecentManager, "get_default", lambda: fake_manager)

        result = recent_docs_for_app("firefox.desktop", launcher=MagicMock(), limit=10)
        assert len(result) == 3
        assert result[0].uri == "file:///new.txt"
        assert result[1].uri == "file:///mid.txt"
        assert result[2].uri == "file:///old.txt"

    def test_filters_empty_mime_type(self, monkeypatch):
        import docking.platform.recent_docs as mod

        monkeypatch.setattr(
            mod.desktop_entries,
            "resolve_app_info",
            lambda desktop_id, **kw: SimpleNamespace(
                app_info=SimpleNamespace(get_display_name=lambda: "Firefox")
            ),
        )
        items = [
            _FakeRecentItem(
                "file:///no-mime.txt",
                "no-mime.txt",
                "",
                1000,
                is_local=True,
                exists=True,
                has_application=True,
            ),
            _FakeRecentItem(
                "file:///has-mime.txt",
                "has-mime.txt",
                "text/plain",
                500,
                is_local=True,
                exists=True,
                has_application=True,
            ),
        ]
        fake_manager = MagicMock()
        fake_manager.get_items.return_value = items
        monkeypatch.setattr(mod.Gtk.RecentManager, "get_default", lambda: fake_manager)

        result = recent_docs_for_app("firefox.desktop", launcher=MagicMock(), limit=10)
        assert len(result) == 1
        assert result[0].uri == "file:///has-mime.txt"
