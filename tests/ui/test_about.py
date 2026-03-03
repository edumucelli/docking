"""Tests for About dialog controller."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.about as about_mod  # noqa: E402


class FakeAboutDialog:
    def __init__(self, **_kwargs) -> None:
        self.show_count = 0
        self.hidden = False
        self.destroyed = False
        self.callbacks: dict[str, object] = {}

    def set_program_name(self, _value: str) -> None:
        return

    def set_version(self, _value: str) -> None:
        return

    def set_comments(self, _value: str) -> None:
        return

    def set_website(self, _value: str) -> None:
        return

    def set_website_label(self, _value: str) -> None:
        return

    def set_logo_icon_name(self, _value: str) -> None:
        return

    def set_authors(self, _value: list[str]) -> None:
        return

    def connect(self, signal: str, callback) -> None:
        self.callbacks[signal] = callback

    def show_all(self) -> None:
        self.show_count += 1

    def hide(self) -> None:
        self.hidden = True

    def destroy(self) -> None:
        self.destroyed = True


class TestAboutDialogController:
    def test_show_reuses_single_dialog(self, monkeypatch):
        # Given
        monkeypatch.setattr(
            about_mod,
            "Gtk",
            type("FakeGtk", (), {"AboutDialog": FakeAboutDialog, "Window": object}),
        )
        monkeypatch.setattr(about_mod, "pkg_version", lambda _name: "1.2.3")
        controller = about_mod.AboutDialogController(parent=object())

        # When
        controller.show()
        first = controller._dialog
        controller.show()

        # Then
        assert first is not None
        assert controller._dialog is first
        assert first.show_count == 2

    def test_response_and_hide_destroy_dialog(self, monkeypatch):
        # Given
        monkeypatch.setattr(
            about_mod,
            "Gtk",
            type("FakeGtk", (), {"AboutDialog": FakeAboutDialog, "Window": object}),
        )
        monkeypatch.setattr(about_mod, "pkg_version", lambda _name: "1.2.3")
        controller = about_mod.AboutDialogController(parent=object())
        controller.show()
        dialog = controller._dialog
        assert dialog is not None

        # When
        controller._on_response(dialog, 0)
        controller._on_hide(dialog)

        # Then
        assert dialog.hidden is True
        assert dialog.destroyed is True
        assert controller._dialog is None

    def test_version_fallback_when_package_missing(self, monkeypatch):
        # Given
        monkeypatch.setattr(
            about_mod,
            "pkg_version",
            MagicMock(side_effect=about_mod.PackageNotFoundError),
        )
        controller = about_mod.AboutDialogController(parent=object())

        # When
        result = controller._project_version()

        # Then
        assert result == about_mod.PROJECT_VERSION_FALLBACK
