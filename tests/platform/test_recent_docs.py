"""Tests for recent_docs module - document-to-app association."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import docking.platform.recent_docs as recent_docs_mod
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)
from docking.platform.recent_docs import RecentDoc, recent_docs_for_app

_DEFAULT_APPLICATION = object()


def _patch_one_shot_registry(
    monkeypatch,
    application: object | None = _DEFAULT_APPLICATION,
) -> MagicMock:
    registry = MagicMock()
    registry.refresh.return_value = True
    registry.resolve.return_value = (
        SimpleNamespace(name="Firefox")
        if application is _DEFAULT_APPLICATION
        else application
    )
    monkeypatch.setattr(
        recent_docs_mod,
        "ApplicationRegistry",
        MagicMock(return_value=registry),
    )
    return registry


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
        result = recent_docs_for_app("", limit=10)
        assert result == []

    def test_nonexistent_desktop_entry_returns_empty(self, monkeypatch):
        registry = _patch_one_shot_registry(monkeypatch, None)

        result = recent_docs_for_app("missing.desktop", limit=10)

        assert result == []
        recent_docs_mod.ApplicationRegistry.assert_called_once_with()
        registry.refresh.assert_called_once_with()
        registry.resolve.assert_called_once_with(
            "missing.desktop",
            log_failures=False,
        )

    def test_empty_recent_store_returns_empty(self, monkeypatch):
        import docking.platform.recent_docs as mod

        _patch_one_shot_registry(monkeypatch)
        fake_manager = MagicMock()
        fake_manager.get_items.return_value = []
        monkeypatch.setattr(mod.Gtk.RecentManager, "get_default", lambda: fake_manager)

        result = recent_docs_for_app("firefox.desktop", limit=10)
        assert result == []

    def test_injected_registry_resolves_without_refreshing_borrowed_state(
        self,
        monkeypatch,
    ):
        registry = MagicMock()
        registry.resolve.return_value = SimpleNamespace(name="Firefox")
        fake_manager = MagicMock()
        fake_manager.get_items.return_value = []
        monkeypatch.setattr(
            recent_docs_mod.Gtk.RecentManager,
            "get_default",
            lambda: fake_manager,
        )

        assert (
            recent_docs_for_app(
                "firefox.desktop",
                limit=10,
                registry=registry,
            )
            == []
        )
        registry.resolve.assert_called_once_with(
            "firefox.desktop",
            log_failures=False,
        )
        registry.refresh.assert_not_called()

    def test_legacy_positional_launcher_and_limit_are_accepted_as_no_op(
        self,
        monkeypatch,
    ):
        registry = MagicMock()
        registry.resolve.return_value = SimpleNamespace(name="Firefox")
        legacy_launcher = MagicMock()
        legacy_launcher.resolve.side_effect = AssertionError(
            "deprecated launcher must not resolve applications"
        )
        manager = MagicMock()
        manager.get_items.return_value = [
            _FakeRecentItem(
                f"file:///doc-{index}.txt",
                f"doc-{index}.txt",
                "text/plain",
                1000 - index,
            )
            for index in range(3)
        ]
        monkeypatch.setattr(
            recent_docs_mod.Gtk.RecentManager,
            "get_default",
            lambda: manager,
        )

        result = recent_docs_for_app(
            "firefox.desktop",
            legacy_launcher,
            1,
            registry=registry,
        )

        assert [document.name for document in result] == ["doc-0.txt"]
        legacy_launcher.resolve.assert_not_called()
        registry.resolve.assert_called_once_with(
            "firefox.desktop",
            log_failures=False,
        )

    def test_legacy_launcher_keyword_is_accepted_as_no_op(self, monkeypatch):
        registry = MagicMock()
        registry.resolve.return_value = SimpleNamespace(name="Firefox")
        legacy_launcher = MagicMock()
        legacy_launcher.resolve.side_effect = AssertionError(
            "deprecated launcher must not resolve applications"
        )
        manager = MagicMock()
        manager.get_items.return_value = []
        monkeypatch.setattr(
            recent_docs_mod.Gtk.RecentManager,
            "get_default",
            lambda: manager,
        )

        assert (
            recent_docs_for_app(
                desktop_id="firefox.desktop",
                launcher=legacy_launcher,
                registry=registry,
                limit=2,
            )
            == []
        )
        legacy_launcher.resolve.assert_not_called()
        registry.resolve.assert_called_once_with(
            "firefox.desktop",
            log_failures=False,
        )

    def test_second_positional_integer_remains_the_limit(self, monkeypatch):
        registry = MagicMock()
        registry.resolve.return_value = SimpleNamespace(name="Firefox")
        manager = MagicMock()
        manager.get_items.return_value = [
            _FakeRecentItem(
                f"file:///doc-{index}.txt",
                f"doc-{index}.txt",
                "text/plain",
                1000 - index,
            )
            for index in range(3)
        ]
        monkeypatch.setattr(
            recent_docs_mod.Gtk.RecentManager,
            "get_default",
            lambda: manager,
        )

        result = recent_docs_for_app("firefox.desktop", 1, registry=registry)

        assert [document.name for document in result] == ["doc-0.txt"]

    def test_filters_non_local_files(self, monkeypatch):
        import docking.platform.recent_docs as mod

        _patch_one_shot_registry(monkeypatch)
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

        result = recent_docs_for_app("firefox.desktop", limit=10)
        assert len(result) == 1
        assert result[0].uri == "file:///local/doc.txt"

    def test_filters_nonexistent_files(self, monkeypatch):
        import docking.platform.recent_docs as mod

        _patch_one_shot_registry(monkeypatch)
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

        result = recent_docs_for_app("firefox.desktop", limit=10)
        assert len(result) == 1
        assert result[0].uri == "file:///alive/doc.txt"

    def test_filters_wrong_application(self, monkeypatch):
        import docking.platform.recent_docs as mod

        _patch_one_shot_registry(monkeypatch)
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

        result = recent_docs_for_app("firefox.desktop", limit=10)
        assert len(result) == 1
        assert result[0].uri == "file:///firefox/doc.txt"

    def test_respects_limit(self, monkeypatch):
        import docking.platform.recent_docs as mod

        _patch_one_shot_registry(monkeypatch)
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

        result = recent_docs_for_app("firefox.desktop", limit=5)
        assert len(result) == 5

    def test_sorts_by_modified_descending(self, monkeypatch):
        import docking.platform.recent_docs as mod

        _patch_one_shot_registry(monkeypatch)
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

        result = recent_docs_for_app("firefox.desktop", limit=10)
        assert len(result) == 3
        assert result[0].uri == "file:///new.txt"
        assert result[1].uri == "file:///mid.txt"
        assert result[2].uri == "file:///old.txt"

    def test_filters_empty_mime_type(self, monkeypatch):
        import docking.platform.recent_docs as mod

        _patch_one_shot_registry(monkeypatch)
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

        result = recent_docs_for_app("firefox.desktop", limit=10)
        assert len(result) == 1
        assert result[0].uri == "file:///has-mime.txt"

    def test_canonical_application_uses_exact_metadata_without_reresolve(
        self,
        monkeypatch,
    ):
        import docking.platform.recent_docs as mod

        application = ApplicationInfo(
            desktop_id="org.example.Writer.desktop",
            name="Canonical Writer",
            declared_icon="org.example.Writer",
            wm_class="Writer",
            exec_line="writer",
            origin=ApplicationOrigin.INSTALLED,
            location=ApplicationLocation.SANDBOX,
            desktop_file=Path("/usr/share/applications/org.example.Writer.desktop"),
            executable_path=None,
            aliases=("writer",),
            visible=True,
            has_gio_source=True,
        )
        item = _FakeRecentItem(
            "file:///tmp/exact.odt",
            "exact.odt",
            "application/vnd.oasis.opendocument.text",
            1234,
        )
        item.has_application = MagicMock(return_value=True)
        manager = MagicMock()
        manager.get_items.return_value = [item]
        monkeypatch.setattr(mod.Gtk.RecentManager, "get_default", lambda: manager)
        monkeypatch.setattr(
            mod,
            "ApplicationRegistry",
            MagicMock(side_effect=AssertionError("canonical metadata was re-resolved")),
        )

        assert recent_docs_for_app(application, limit=1) == [
            RecentDoc(
                uri="file:///tmp/exact.odt",
                name="exact.odt",
                mime_type="application/vnd.oasis.opendocument.text",
                modified=1234,
            )
        ]
        item.has_application.assert_called_once_with("Canonical Writer")
