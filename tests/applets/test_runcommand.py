"""Tests for the Run Application applet."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.runcommand.applet as runcommand_applet_mod
from docking.applets import get_applet_catalog, load_applet_class
from docking.applets.runcommand.applet import RunCommandApplet
from docking.applets.runcommand.state import (
    app_command_text,
    app_description,
    match_application,
    normalize_history,
    updated_history,
)
from docking.core.config import Config
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
    TransientApplicationInfo,
)


def _fake_app(
    name: str,
    *,
    desktop_id: str | None = None,
    command: str = "",
    description: str = "",
    generic_name: str = "",
    icon: str = "",
    has_gio_source: bool = True,
) -> ApplicationInfo:
    identifier = desktop_id or f"org.example.{name.replace(' ', '')}.desktop"
    return ApplicationInfo(
        desktop_id=identifier,
        name=name,
        declared_icon=icon,
        wm_class=identifier.removesuffix(".desktop"),
        exec_line=command,
        origin=ApplicationOrigin.INSTALLED,
        location=(
            ApplicationLocation.SANDBOX if has_gio_source else ApplicationLocation.HOST
        ),
        desktop_file=None,
        executable_path=None,
        aliases=(),
        visible=True,
        has_gio_source=has_gio_source,
        generic_name=generic_name,
        description=description,
    )


class _FakeEntry:
    def __init__(self, text: str = "") -> None:
        self._text = text
        self.position = None
        self.focused = False

    def get_text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text

    def set_position(self, position: int) -> None:
        self.position = position

    def grab_focus(self) -> None:
        self.focused = True


class _FakeIcon:
    def __init__(self) -> None:
        self.gicon = None
        self.icon_name = None
        self.pixel_size = None

    def set_from_gicon(self, gicon, _size) -> None:
        self.gicon = gicon

    def set_from_icon_name(self, icon_name: str, _size) -> None:
        self.icon_name = icon_name

    def set_pixel_size(self, size: int) -> None:
        self.pixel_size = size


class _FakeLabel:
    def __init__(self) -> None:
        self.text = ""

    def set_text(self, text: str) -> None:
        self.text = text


class _FakeButton:
    def __init__(self) -> None:
        self.sensitive = None

    def set_sensitive(self, value: bool) -> None:
        self.sensitive = value


class _FakeCheck:
    def __init__(self, active: bool = False) -> None:
        self.active = active

    def get_active(self) -> bool:
        return self.active


class _FakeDialog:
    def __init__(self) -> None:
        self.hidden = False

    def hide(self) -> None:
        self.hidden = True


class _FakeRow:
    def __init__(
        self,
        app: ApplicationInfo | TransientApplicationInfo,
    ) -> None:
        self.app = app
        self.visible = True

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False


class _Registry:
    def __init__(
        self,
        applications: tuple[ApplicationInfo, ...],
        unidentified: tuple[TransientApplicationInfo, ...] = (),
    ) -> None:
        self.applications = applications
        self.unidentified = unidentified

    def snapshot(self) -> tuple[ApplicationInfo, ...]:
        return self.applications

    def unidentified_snapshot(
        self,
    ) -> tuple[TransientApplicationInfo, ...]:
        return self.unidentified

    def _gio_handle_for(self, _desktop_id: str) -> None:
        return None

    def _gio_handle_for_unidentified(self, _listing_key: str) -> None:
        return None


def _make_applet(
    *,
    registry: _Registry | None = None,
    launcher: object | None = None,
) -> RunCommandApplet:
    return RunCommandApplet(
        48,
        config=Config(),
        application_registry=registry or _Registry(()),  # ty: ignore[invalid-argument-type]
        application_launcher=launcher or MagicMock(),  # ty: ignore[invalid-argument-type]
    )


class TestRunCommandState:
    def test_app_description_handles_missing_optional_gio_metadata_methods(self):
        app = _fake_app("Fallback application")

        assert app_description(app) == "Fallback application"

    def test_gio_backed_description_prefers_description_then_generic_name(self):
        described = _fake_app(
            "Described",
            description="Primary description",
            generic_name="Generic description",
        )
        generic = _fake_app("Generic", generic_name="Generic description")

        assert app_description(described) == "Primary description"
        assert app_description(generic) == "Generic description"

    def test_file_only_metadata_keeps_legacy_display_name_fallback(self):
        app = _fake_app(
            "Host File Tool",
            command="/opt/host-file-tool --open %U",
            description="Parsed desktop Comment",
            generic_name="Parsed GenericName",
            has_gio_source=False,
        )

        assert app_command_text(app) == "Host File Tool"
        assert app_description(app) == "Host File Tool"

    def test_idless_listing_preserves_gio_command_and_description_fallbacks(self):
        listing = TransientApplicationInfo(
            listing_key="opaque",
            name="ID-less Tool",
            categories_raw="Utility;",
            declared_icon="",
            desktop_file=None,
            exec_line="idless-tool %U",
            description="Launch an ID-less tool",
            generic_name="Generic ID-less Tool",
        )
        generic_only = TransientApplicationInfo(
            listing_key="generic",
            name="Generic Fallback",
            categories_raw="Utility;",
            declared_icon="",
            desktop_file=None,
            exec_line="",
            description="",
            generic_name="Generic description",
        )

        assert app_command_text(listing) == "idless-tool"
        assert app_description(listing) == "Launch an ID-less tool"
        assert app_command_text(generic_only) == "Generic Fallback"
        assert app_description(generic_only) == "Generic description"

    def test_normalize_history_filters_deduplicates_and_caps(self):
        raw = [" firefox ", "", "calc", "firefox", 1, *[f"cmd{i}" for i in range(30)]]

        history = normalize_history(raw)

        assert history[:3] == ["firefox", "calc", "cmd0"]
        assert len(history) == 20

    def test_updated_history_promotes_command(self):
        assert updated_history(history=["one", "two"], command=" two ") == [
            "two",
            "one",
        ]

    def test_gio_backed_command_text_removes_desktop_placeholders(self):
        app = _fake_app("Atril", command="atril %U")

        assert app_command_text(app) == "atril"

    def test_match_application_exact_then_unique_prefix(self):
        calc = _fake_app("Calculator")
        calendar = _fake_app("Calendar")
        firefox = _fake_app("Firefox")

        assert match_application(apps=[calc, calendar, firefox], text="fire") is firefox
        assert match_application(apps=[calc, calendar], text="calc") is calc
        assert match_application(apps=[calc, calendar], text="cal") is None


class TestRunCommandApplet:
    def test_catalog_discovers_applet(self):
        get_applet_catalog.cache_clear()
        load_applet_class.cache_clear()

        assert "runcommand" in get_applet_catalog()
        assert load_applet_class("runcommand") is RunCommandApplet

    def test_creates_with_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None

    def test_renders_at_various_sizes(self):
        applet = _make_applet()
        for size in [32, 48, 64]:
            assert applet.create_icon(size) is not None

    def test_run_with_file_ignores_late_click_after_dialog_is_destroyed(
        self, monkeypatch
    ):
        applet = _make_applet()
        chooser = MagicMock()
        monkeypatch.setattr(runcommand_applet_mod.Gtk, "FileChooserDialog", chooser)

        applet._on_run_with_file(MagicMock())

        chooser.assert_not_called()

    def test_select_application_updates_entry_description_and_icon(self):
        applet = _make_applet()
        app = _fake_app(
            "Atril Document Viewer",
            command="atril %U",
            description="View documents",
            icon="atril-icon",
        )
        applet._entry = _FakeEntry()
        applet._left_icon = _FakeIcon()
        applet._description_label = _FakeLabel()
        applet._run_button = _FakeButton()

        applet._select_application(app)

        assert applet._entry.get_text() == "atril"
        assert applet._entry.position == -1
        assert applet._left_icon.gicon.to_string() == "atril-icon"
        assert applet._description_label.text == "View documents"
        assert applet._run_button.sensitive is True

    def test_typing_matching_app_name_updates_left_icon(self):
        applet = _make_applet()
        applet._apps = [_fake_app("Calculator", description="Calculate", icon="calc")]
        applet._app_rows = [_FakeRow(applet._apps[0])]
        applet._entry = _FakeEntry("calc")
        applet._left_icon = _FakeIcon()
        applet._description_label = _FakeLabel()
        applet._run_button = _FakeButton()

        applet._on_entry_changed(applet._entry)

        assert applet._selected_app is applet._apps[0]
        assert applet._left_icon.gicon.to_string() == "calc"
        assert applet._description_label.text == "Calculate"

    def test_typing_unknown_text_restores_default_left_icon(self):
        applet = _make_applet()
        applet._apps = [_fake_app("Calculator", icon="calc")]
        applet._app_rows = [_FakeRow(applet._apps[0])]
        applet._entry = _FakeEntry("unknown")
        applet._left_icon = _FakeIcon()
        applet._description_label = _FakeLabel()
        applet._run_button = _FakeButton()

        applet._on_entry_changed(applet._entry)

        assert applet._selected_app is None
        assert applet._left_icon.icon_name == "system-run"
        assert applet._run_button.sensitive is True

    def test_typing_filters_application_rows_without_resizing_list(self):
        applet = _make_applet()
        engrampa = _fake_app("Engrampa Archive Manager", command="engrampa %U")
        cairo = _fake_app("Cairo-Dock", command="cairo-dock")
        applet._apps = [engrampa, cairo]
        applet._app_rows = [_FakeRow(engrampa), _FakeRow(cairo)]
        applet._entry = _FakeEntry("engrampa")
        applet._left_icon = _FakeIcon()
        applet._description_label = _FakeLabel()
        applet._run_button = _FakeButton()

        applet._on_entry_changed(applet._entry)

        assert applet._app_rows[0].visible is True
        assert applet._app_rows[1].visible is False

    def test_refresh_lists_visible_and_idless_registry_entries(self, monkeypatch):
        class _Row:
            def __init__(self, app):
                self.app = app

            def add(self, _child):
                return

            def show(self):
                return

            def hide(self):
                return

        identified = _fake_app("Calculator")
        idless = TransientApplicationInfo(
            listing_key="gio-idless:1",
            name="ID-less",
            categories_raw="Utility;",
            declared_icon="",
            desktop_file=None,
        )
        registry = _Registry((identified,), (idless,))
        applet = _make_applet(registry=registry)
        applet._app_list = MagicMock()
        applet._app_list.get_children.return_value = []
        applet._build_app_row = MagicMock(return_value=MagicMock())
        monkeypatch.setattr(runcommand_applet_mod, "_ApplicationRow", _Row)

        applet._refresh_app_list()

        assert applet._apps == [identified, idless]
        assert len(applet._app_rows) == 2

    def test_run_current_launches_selected_application(self, monkeypatch):
        launcher = MagicMock()
        launcher.launch.return_value = True
        applet = _make_applet(launcher=launcher)
        app = _fake_app(
            "Calculator",
            desktop_id="org.gnome.Calculator.desktop",
            command="gnome-calculator",
        )
        applet._entry = _FakeEntry("gnome-calculator")
        applet._selected_app = app
        applet._selected_entry_text = "gnome-calculator"
        applet._dialog = _FakeDialog()
        command_launch = MagicMock(return_value=True)
        monkeypatch.setattr(
            runcommand_applet_mod.commands,
            "launch_command",
            command_launch,
        )

        applet._run_current()

        launcher.launch.assert_called_once_with("org.gnome.Calculator.desktop")
        command_launch.assert_not_called()
        assert applet._history == ["gnome-calculator"]
        assert applet._dialog.hidden is True

    def test_run_current_terminal_mode_overrides_matched_application(self, monkeypatch):
        launcher = MagicMock()
        launcher.launch.return_value = True
        applet = _make_applet(launcher=launcher)
        app = _fake_app("Calculator", command="gnome-calculator")
        applet._entry = _FakeEntry("gnome-calculator")
        applet._terminal_check = _FakeCheck(active=True)
        applet._selected_app = app
        applet._selected_entry_text = "gnome-calculator"
        applet._dialog = _FakeDialog()
        launch_command = MagicMock(return_value=True)
        monkeypatch.setattr(
            runcommand_applet_mod.commands,
            "launch_command",
            launch_command,
        )

        applet._run_current()

        launch_command.assert_called_once_with(
            command="gnome-calculator",
            run_in_terminal=True,
        )
        launcher.launch.assert_not_called()

    def test_run_current_launches_shell_command_when_no_app_matches(self, monkeypatch):
        applet = _make_applet()
        applet._apps = [_fake_app("Calculator")]
        applet._entry = _FakeEntry("echo ok")
        applet._terminal_check = _FakeCheck(active=True)
        applet._dialog = _FakeDialog()
        launch = MagicMock(return_value=True)
        monkeypatch.setattr(
            runcommand_applet_mod.commands,
            "launch_command",
            launch,
        )

        applet._run_current()

        launch.assert_called_once_with(command="echo ok", run_in_terminal=True)
        assert applet._history == ["echo ok"]
        assert applet._dialog.hidden is True

    def test_run_current_launches_idless_selection_by_opaque_key(self):
        launcher = MagicMock()
        launcher.launch_listing.return_value = True
        applet = _make_applet(launcher=launcher)
        app = TransientApplicationInfo(
            listing_key="gio-idless:3",
            name="ID-less Tool",
            categories_raw="Utility;",
            declared_icon="",
            desktop_file=None,
        )
        applet._entry = _FakeEntry("ID-less Tool")
        applet._selected_app = app
        applet._selected_entry_text = "ID-less Tool"
        applet._run_current()

        launcher.launch_listing.assert_called_once_with("gio-idless:3")

    def test_show_dialog_refreshes_apps_and_history(self, monkeypatch):
        applet = _make_applet()
        dialog = SimpleNamespace(
            show_all=MagicMock(),
            present=MagicMock(),
            get_visible=lambda: False,
        )
        applet._dialog = dialog
        applet._entry = _FakeEntry()
        applet._run_button = _FakeButton()
        applet._history = ["echo ok"]
        monkeypatch.setattr(applet, "_refresh_app_list", MagicMock())
        monkeypatch.setattr(applet, "_sync_entry_history", MagicMock())
        monkeypatch.setattr(applet, "_apply_app_filter", MagicMock())

        applet._show_dialog()

        applet._refresh_app_list.assert_called_once_with()
        applet._sync_entry_history.assert_called_once_with()
        dialog.show_all.assert_called_once_with()
        applet._apply_app_filter.assert_called_once_with("")
        dialog.present.assert_called_once_with()
