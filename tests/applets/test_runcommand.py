"""Tests for the Run Application applet."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.runcommand.applet as runcommand_applet_mod
import docking.applets.runcommand.state as runcommand_state_mod
from docking.applets import get_applet_catalog, load_applet_class
from docking.applets.runcommand.applet import RunCommandApplet
from docking.applets.runcommand.state import (
    TERMINAL_CANDIDATES,
    _flatpak_host_terminal_name,
    app_command_text,
    app_description,
    append_file_argument,
    build_shell_argv,
    build_terminal_argv,
    launch_command,
    match_application,
    normalize_history,
    updated_history,
)


class _FakeAppInfo:
    def __init__(self, command: str = "", description: str = "") -> None:
        self.command = command
        self.description = description

    def get_commandline(self) -> str:
        return self.command

    def get_description(self) -> str:
        return self.description


class _FakeApp:
    def __init__(
        self,
        name: str,
        *,
        command: str = "",
        description: str = "",
        icon: object | None = None,
    ) -> None:
        self.name = name
        self.app_info = _FakeAppInfo(command=command, description=description)
        self.icon = icon
        self.launched = False

    def get_display_name(self) -> str:
        return self.name

    def get_icon(self) -> object | None:
        return self.icon

    def launch(self, _files: list[object], _context: object | None) -> None:
        self.launched = True


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
    def __init__(self, app: _FakeApp) -> None:
        self.app = app
        self.visible = True

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False


def _make_applet() -> RunCommandApplet:
    return RunCommandApplet(48)


class TestRunCommandState:
    def test_app_description_handles_missing_optional_gio_metadata_methods(self):
        app = _FakeApp("Fallback application")

        assert app_description(app) == "Fallback application"

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

    def test_build_shell_argv(self):
        assert build_shell_argv(command="echo ok", shell="/bin/zsh") == [
            "/bin/zsh",
            "-lc",
            "echo ok",
        ]

    def test_build_terminal_argv_uses_first_available_terminal(self):
        def resolver(name: str) -> str | None:
            return name if name == "x-terminal-emulator" else None

        assert build_terminal_argv(
            command="echo ok",
            shell="/bin/sh",
            resolver=resolver,
        ) == [
            "x-terminal-emulator",
            "-e",
            "/bin/sh",
            "-lc",
            "echo ok",
        ]

    def test_build_terminal_argv_supports_multi_arg_terminal_prefix(self):
        def resolver(name: str) -> str | None:
            return name if name == "wezterm" else None

        assert build_terminal_argv(
            command="echo ok",
            shell="/bin/sh",
            resolver=resolver,
        ) == [
            "wezterm",
            "start",
            "--",
            "/bin/sh",
            "-lc",
            "echo ok",
        ]

    def test_build_terminal_argv_supports_command_string_terminals(self):
        def resolver(name: str) -> str | None:
            return name if name == "xfce4-terminal" else None

        assert build_terminal_argv(
            command="echo ok",
            shell="/bin/sh",
            resolver=resolver,
        ) == [
            "xfce4-terminal",
            "-e",
            "/bin/sh -lc 'echo ok'",
        ]

    def test_terminal_candidates_cover_common_desktops_and_wayland(self):
        names = {candidate.executable for candidate in TERMINAL_CANDIDATES}

        assert {
            "x-terminal-emulator",
            "gnome-terminal",
            "kgx",
            "ptyxis",
            "konsole",
            "qterminal",
            "foot",
            "kitty",
            "alacritty",
            "wezterm",
            "xterm",
        }.issubset(names)

    def test_flatpak_host_terminal_lookup_is_cached_single_scan(self, monkeypatch):
        _flatpak_host_terminal_name.cache_clear()
        calls = []

        monkeypatch.setattr(
            runcommand_state_mod.flatpak,
            "spawn_path",
            lambda: "/usr/bin/flatpak-spawn",
        )
        monkeypatch.setattr(
            runcommand_state_mod.flatpak,
            "host_command",
            lambda cmd, sanitize_env: ["flatpak-spawn", *cmd],
        )

        def run(cmd, **_kwargs):
            calls.append(cmd)
            return SimpleNamespace(stdout="konsole\n")

        monkeypatch.setattr(runcommand_state_mod.subprocess, "run", run)

        assert _flatpak_host_terminal_name() == "konsole"
        assert _flatpak_host_terminal_name() == "konsole"
        assert len(calls) == 1
        _flatpak_host_terminal_name.cache_clear()

    def test_launch_command_rejects_empty_command(self):
        popen = MagicMock()

        assert launch_command(command=" ", run_in_terminal=False, popen=popen) is False
        popen.assert_not_called()

    def test_launch_command_starts_shell_process(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/zsh")
        popen = MagicMock()

        monkeypatch.setattr(
            "docking.applets.runcommand.state.flatpak.host_command",
            lambda argv: None,
        )

        assert launch_command(command="echo ok", run_in_terminal=False, popen=popen)

        argv = popen.call_args.args[0]
        assert argv == ["/bin/zsh", "-lc", "echo ok"]
        assert popen.call_args.kwargs["start_new_session"] is True

    def test_append_file_argument_quotes_path(self):
        assert append_file_argument(command="atril", path="/tmp/report one.pdf") == (
            "atril '/tmp/report one.pdf'"
        )

    def test_app_command_text_removes_desktop_placeholders(self):
        app = _FakeApp("Atril", command="atril %U")

        assert app_command_text(app) == "atril"

    def test_match_application_exact_then_unique_prefix(self):
        calc = _FakeApp("Calculator")
        calendar = _FakeApp("Calendar")
        firefox = _FakeApp("Firefox")

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
        app = _FakeApp(
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
        assert applet._left_icon.gicon == "atril-icon"
        assert applet._description_label.text == "View documents"
        assert applet._run_button.sensitive is True

    def test_typing_matching_app_name_updates_left_icon(self):
        applet = _make_applet()
        applet._apps = [_FakeApp("Calculator", description="Calculate", icon="calc")]
        applet._app_rows = [_FakeRow(applet._apps[0])]
        applet._entry = _FakeEntry("calc")
        applet._left_icon = _FakeIcon()
        applet._description_label = _FakeLabel()
        applet._run_button = _FakeButton()

        applet._on_entry_changed(applet._entry)

        assert applet._selected_app is applet._apps[0]
        assert applet._left_icon.gicon == "calc"
        assert applet._description_label.text == "Calculate"

    def test_typing_unknown_text_restores_default_left_icon(self):
        applet = _make_applet()
        applet._apps = [_FakeApp("Calculator", icon="calc")]
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
        engrampa = _FakeApp("Engrampa Archive Manager", command="engrampa %U")
        cairo = _FakeApp("Cairo-Dock", command="cairo-dock")
        applet._apps = [engrampa, cairo]
        applet._app_rows = [_FakeRow(engrampa), _FakeRow(cairo)]
        applet._entry = _FakeEntry("engrampa")
        applet._left_icon = _FakeIcon()
        applet._description_label = _FakeLabel()
        applet._run_button = _FakeButton()

        applet._on_entry_changed(applet._entry)

        assert applet._app_rows[0].visible is True
        assert applet._app_rows[1].visible is False

    def test_run_current_launches_selected_application(self, monkeypatch):
        applet = _make_applet()
        app = _FakeApp("Calculator", command="gnome-calculator")
        applet._entry = _FakeEntry("gnome-calculator")
        applet._selected_app = app
        applet._selected_entry_text = "gnome-calculator"
        applet._dialog = _FakeDialog()
        launch = MagicMock(return_value=True)
        monkeypatch.setattr(runcommand_applet_mod, "launch_application", launch)

        applet._run_current()

        launch.assert_called_once_with(app)
        assert applet._history == ["gnome-calculator"]
        assert applet._dialog.hidden is True

    def test_run_current_terminal_mode_overrides_matched_application(self, monkeypatch):
        applet = _make_applet()
        app = _FakeApp("Calculator", command="gnome-calculator")
        applet._entry = _FakeEntry("gnome-calculator")
        applet._terminal_check = _FakeCheck(active=True)
        applet._selected_app = app
        applet._selected_entry_text = "gnome-calculator"
        applet._dialog = _FakeDialog()
        launch_command = MagicMock(return_value=True)
        launch_application = MagicMock(return_value=True)
        monkeypatch.setattr(runcommand_applet_mod, "launch_command", launch_command)
        monkeypatch.setattr(
            runcommand_applet_mod,
            "launch_application",
            launch_application,
        )

        applet._run_current()

        launch_command.assert_called_once_with(
            command="gnome-calculator",
            run_in_terminal=True,
        )
        launch_application.assert_not_called()

    def test_run_current_launches_shell_command_when_no_app_matches(self, monkeypatch):
        applet = _make_applet()
        applet._apps = [_FakeApp("Calculator")]
        applet._entry = _FakeEntry("echo ok")
        applet._terminal_check = _FakeCheck(active=True)
        applet._dialog = _FakeDialog()
        launch = MagicMock(return_value=True)
        monkeypatch.setattr(runcommand_applet_mod, "launch_command", launch)

        applet._run_current()

        launch.assert_called_once_with(command="echo ok", run_in_terminal=True)
        assert applet._history == ["echo ok"]
        assert applet._dialog.hidden is True

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
