"""Tests for About dialog controller."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    gi_mock.repository.GLib.markup_escape_text.side_effect = lambda text: text
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
        self.website = None
        self.website_label = None
        self.logo_icon_name = None

    def set_program_name(self, _value: str) -> None:
        return

    def set_version(self, value: str) -> None:
        self.version = value

    def set_comments(self, _value: str) -> None:
        return

    def set_website(self, value: str) -> None:
        self.website = value

    def set_website_label(self, value: str) -> None:
        self.website_label = value

    def set_logo_icon_name(self, value: str) -> None:
        self.logo_icon_name = value

    def set_authors(self, _value: list[str]) -> None:
        return

    def set_license_type(self, value) -> None:
        self.license_type = value

    def set_license(self, value: str) -> None:
        self.license_text = value

    def set_wrap_license(self, value: bool) -> None:
        self.wrap_license = value

    def add_button(self, label: str, _response) -> FakeButton:
        button = FakeButton(label=label)
        self.buttons.append(button)
        return button

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


def _fake_gtk():
    return type(
        "FakeGtk",
        (),
        {
            "AboutDialog": FakeAboutDialog,
            "Button": FakeButton,
            "License": type("FakeLicense", (), {"GPL_3_0": "gpl3"}),
            "ResponseType": type("FakeResponseType", (), {"HELP": 1}),
            "Window": object,
        },
    )


class TestAboutDialogController:
    def test_show_reuses_single_dialog(self, monkeypatch):
        # Given
        monkeypatch.setattr(
            about_mod,
            "Gtk",
            _fake_gtk(),
        )
        monkeypatch.setattr(about_mod, "pkg_version", lambda _name: "1.2.3")
        register_tooltip_blocker = MagicMock()
        controller = about_mod.AboutDialogController(
            parent=object(),
            register_tooltip_blocker=register_tooltip_blocker,
        )

        # When
        controller.show()
        first = controller._dialog
        controller.show()

        # Then
        assert first is not None
        assert controller._dialog is first
        assert first.show_count == 2
        register_tooltip_blocker.assert_called_once_with(first)

    def test_response_and_hide_destroy_dialog(self, monkeypatch):
        # Given
        monkeypatch.setattr(
            about_mod,
            "Gtk",
            _fake_gtk(),
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
            _fake_gtk(),
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
            _fake_gtk(),
        )
        monkeypatch.setattr(about_mod, "pkg_version", lambda _name: "9.9.9")
        controller = about_mod.AboutDialogController(parent=object())

        controller.show()
        dialog = controller._dialog
        assert dialog is not None

        assert dialog.version == about_mod.docking_version

    def test_show_uses_flatpak_id_as_logo_icon_when_available(self, monkeypatch):
        monkeypatch.setattr(about_mod, "Gtk", _fake_gtk())
        monkeypatch.setenv("FLATPAK_ID", "cc.docking.Docking")
        controller = about_mod.AboutDialogController(parent=object())

        controller.show()
        dialog = controller._dialog
        assert dialog is not None

        assert dialog.logo_icon_name == "cc.docking.Docking"

    def test_show_uses_legacy_logo_icon_outside_flatpak(self, monkeypatch):
        monkeypatch.setattr(about_mod, "Gtk", _fake_gtk())
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        controller = about_mod.AboutDialogController(parent=object())

        controller.show()
        dialog = controller._dialog
        assert dialog is not None

        assert dialog.logo_icon_name == about_mod.DEFAULT_LOGO_ICON_NAME

    def test_show_sets_website_and_github_button(self, monkeypatch):
        monkeypatch.setattr(about_mod, "Gtk", _fake_gtk())
        monkeypatch.setattr(about_mod, "pkg_version", lambda _name: "1.2.3")
        controller = about_mod.AboutDialogController(parent=object())

        controller.show()
        dialog = controller._dialog
        assert dialog is not None

        assert dialog.website == about_mod.PROJECT_WEBSITE_URL
        assert dialog.website_label == "Website"
        assert [button.get_label() for button in dialog.buttons].count("GitHub") == 1

    def test_help_response_opens_project_github(self, monkeypatch):
        monkeypatch.setattr(about_mod, "Gtk", _fake_gtk())
        launch_default_for_uri = MagicMock()
        monkeypatch.setattr(
            about_mod.Gio.AppInfo,
            "launch_default_for_uri",
            launch_default_for_uri,
        )
        controller = about_mod.AboutDialogController(parent=object())
        dialog = FakeAboutDialog()

        controller._on_response(dialog, about_mod.Gtk.ResponseType.HELP)

        launch_default_for_uri.assert_called_once_with(
            about_mod.PROJECT_GITHUB_URL,
            None,
        )
        assert dialog.hidden is False

    def test_license_fallback_when_license_file_missing(self, monkeypatch):
        # Given
        monkeypatch.setattr(
            about_mod,
            "Gtk",
            _fake_gtk(),
        )
        monkeypatch.setattr(about_mod, "PROJECT_LICENSE_PATH", MagicMock())
        about_mod.PROJECT_LICENSE_PATH.read_text.side_effect = OSError
        controller = about_mod.AboutDialogController(parent=object())

        # When
        result = controller._project_license_text()

        # Then
        assert result == about_mod.PROJECT_LICENSE_FALLBACK
