"""Tests for About dialog controller."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.about as about_mod


class FakeButton:
    def __init__(self, label: str) -> None:
        self._label = label
        self.hidden = False

    def get_label(self) -> str:
        return self._label

    def hide(self) -> None:
        self.hidden = True


class FakeActionArea:
    def __init__(self, children: list[object]) -> None:
        self._children = children

    def get_children(self) -> list[object]:
        return self._children


class FakeAboutDialog:
    def __init__(self, **_kwargs) -> None:
        self.show_count = 0
        self.hidden = False
        self.destroyed = False
        self.callbacks: dict[str, object] = {}
        self.version = None
        self.buttons: list[object] = [
            FakeButton(label="Credits"),
            FakeButton(label="License"),
            FakeButton(label="Close"),
        ]
        self._action_area = FakeActionArea(children=self.buttons)
        self.license_type = None
        self.license_text = None
        self.wrap_license = None

    def set_program_name(self, _value: str) -> None:
        return

    def set_version(self, value: str) -> None:
        self.version = value

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

    def set_license_type(self, value) -> None:
        self.license_type = value

    def set_license(self, value: str) -> None:
        self.license_text = value

    def set_wrap_license(self, value: bool) -> None:
        self.wrap_license = value

    def connect(self, signal: str, callback) -> None:
        self.callbacks[signal] = callback

    def get_action_area(self) -> FakeActionArea:
        return self._action_area

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
            type(
                "FakeGtk",
                (),
                {
                    "AboutDialog": FakeAboutDialog,
                    "Button": FakeButton,
                    "License": type("FakeLicense", (), {"GPL_3_0": "gpl3"}),
                    "Window": object,
                },
            ),
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
            type(
                "FakeGtk",
                (),
                {
                    "AboutDialog": FakeAboutDialog,
                    "Button": FakeButton,
                    "License": type("FakeLicense", (), {"GPL_3_0": "gpl3"}),
                    "Window": object,
                },
            ),
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
        assert result == about_mod.docking_version

    def test_source_version_is_preferred_over_installed_metadata(self, monkeypatch):
        monkeypatch.setattr(about_mod, "pkg_version", lambda _name: "9.9.9")
        controller = about_mod.AboutDialogController(parent=object())

        result = controller._project_version()

        assert result == about_mod.docking_version

    def test_show_sets_license(self, monkeypatch):
        # Given
        monkeypatch.setattr(
            about_mod,
            "Gtk",
            type(
                "FakeGtk",
                (),
                {
                    "AboutDialog": FakeAboutDialog,
                    "Button": FakeButton,
                    "License": type("FakeLicense", (), {"GPL_3_0": "gpl3"}),
                    "Window": object,
                },
            ),
        )
        monkeypatch.setattr(about_mod, "pkg_version", lambda _name: "1.2.3")
        monkeypatch.setattr(about_mod, "PROJECT_LICENSE_PATH", MagicMock())
        about_mod.PROJECT_LICENSE_PATH.read_text.return_value = "GPL text"
        controller = about_mod.AboutDialogController(parent=object())

        # When
        controller.show()
        dialog = controller._dialog
        assert dialog is not None

        # Then
        assert dialog.license_type == "gpl3"
        assert dialog.license_text == "GPL text"
        assert dialog.wrap_license is True

    def test_show_uses_source_tree_version(self, monkeypatch):
        monkeypatch.setattr(
            about_mod,
            "Gtk",
            type(
                "FakeGtk",
                (),
                {
                    "AboutDialog": FakeAboutDialog,
                    "Button": FakeButton,
                    "License": type("FakeLicense", (), {"GPL_3_0": "gpl3"}),
                    "Window": object,
                },
            ),
        )
        monkeypatch.setattr(about_mod, "pkg_version", lambda _name: "9.9.9")
        controller = about_mod.AboutDialogController(parent=object())

        controller.show()
        dialog = controller._dialog
        assert dialog is not None

        assert dialog.version == about_mod.docking_version

    def test_license_fallback_when_license_file_missing(self, monkeypatch):
        # Given
        monkeypatch.setattr(
            about_mod,
            "Gtk",
            type(
                "FakeGtk",
                (),
                {
                    "AboutDialog": FakeAboutDialog,
                    "Button": FakeButton,
                    "License": type("FakeLicense", (), {"GPL_3_0": "gpl3"}),
                    "Window": object,
                },
            ),
        )
        monkeypatch.setattr(about_mod, "PROJECT_LICENSE_PATH", MagicMock())
        about_mod.PROJECT_LICENSE_PATH.read_text.side_effect = OSError
        controller = about_mod.AboutDialogController(parent=object())

        # When
        result = controller._project_license_text()

        # Then
        assert result == about_mod.PROJECT_LICENSE_FALLBACK
