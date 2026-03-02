"""Tests for desktop file resolution."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Mock gi before importing launcher only when PyGObject is unavailable.
try:
    import gi  # type: ignore # noqa: F401
except Exception:
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.platform.launcher import (  # noqa: E402
    Launcher,
    get_actions,
    launch,
    launch_action,
)


class TestGetDesktopDirs:
    def test_uses_xdg_data_dirs(self, tmp_path):
        # Given
        apps_dir = tmp_path / "share" / "applications"
        apps_dir.mkdir(parents=True)
        # When
        with patch.dict(os.environ, {"XDG_DATA_DIRS": str(tmp_path / "share")}):
            launcher = Launcher()
        # Then
        assert apps_dir in launcher._desktop_dirs

    def test_includes_user_local(self, tmp_path):
        # Given
        user_apps = tmp_path / ".local" / "share" / "applications"
        user_apps.mkdir(parents=True)
        # When
        with patch.dict(
            os.environ, {"XDG_DATA_HOME": str(tmp_path / ".local" / "share")}
        ):
            launcher = Launcher()
        # Then
        assert user_apps in launcher._desktop_dirs

    def test_skips_nonexistent_dirs(self):
        # Given / When
        with patch.dict(os.environ, {"XDG_DATA_DIRS": "/nonexistent/path"}):
            launcher = Launcher()
        # Then
        assert Path("/nonexistent/path/applications") not in launcher._desktop_dirs


class TestIconCache:
    def test_caches_loaded_icons(self):
        # Given
        launcher = Launcher()
        # When
        icon1 = launcher.load_icon("application-x-executable", 48)
        icon2 = launcher.load_icon("application-x-executable", 48)
        # Then
        assert icon1 is icon2

    def test_different_sizes_cached_separately(self):
        # Given
        launcher = Launcher()
        # When
        launcher.load_icon("application-x-executable", 48)
        launcher.load_icon("application-x-executable", 96)
        # Then
        assert ("application-x-executable", 48) in launcher._icon_cache
        assert ("application-x-executable", 96) in launcher._icon_cache


class TestDesktopActions:
    def test_get_actions_returns_pairs(self):
        # Given a mock DesktopAppInfo with actions
        mock_app = MagicMock()
        mock_app.list_actions.return_value = ["new-window", "new-private"]
        mock_app.get_action_name.side_effect = lambda a: {
            "new-window": "New Window",
            "new-private": "New Incognito Window",
        }[a]

        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            actions = get_actions(desktop_id="chrome.desktop")

        # Then
        assert actions == [
            ("new-window", "New Window"),
            ("new-private", "New Incognito Window"),
        ]

    def test_get_actions_returns_empty_for_unknown(self):
        # Given an unknown desktop id
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=None
        ):
            actions = get_actions(desktop_id="nonexistent.desktop")
        # Then
        assert actions == []

    def test_get_actions_skips_empty_names(self):
        # Given an action with no display name
        mock_app = MagicMock()
        mock_app.list_actions.return_value = ["good", "empty"]
        mock_app.get_action_name.side_effect = lambda a: "Good" if a == "good" else ""

        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            actions = get_actions(desktop_id="app.desktop")
        # Then
        assert actions == [("good", "Good")]

    def test_launch_action_calls_gio(self):
        # Given
        mock_app = MagicMock()
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch_action(desktop_id="chrome.desktop", action_id="new-window")
        # Then
        mock_app.launch_action.assert_called_once_with("new-window", None)

    def test_get_actions_returns_empty_when_gio_raises(self, monkeypatch):
        # Given / When
        from docking.platform import launcher as launcher_mod

        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new",
            side_effect=TypeError,
        ):
            actions = get_actions(desktop_id="broken.desktop")
        # Then
        assert actions == []

    def test_launch_action_ignores_gio_errors(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)
        mock_app = MagicMock()
        mock_app.launch_action.side_effect = RuntimeError("gio fail")

        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch_action(desktop_id="chrome.desktop", action_id="new-window")

        # Then
        mock_app.launch_action.assert_called_once_with("new-window", None)


class TestResolve:
    def test_resolve_uses_gio_desktop_info_directly(self):
        # Given
        launcher = Launcher()
        icon = MagicMock()
        icon.to_string.return_value = "firefox"
        app = MagicMock()
        app.get_startup_wm_class.return_value = "Firefox"
        app.get_commandline.return_value = "/usr/bin/firefox %U"
        app.get_icon.return_value = icon
        app.get_display_name.return_value = "Firefox"

        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=app
        ):
            info = launcher.resolve("firefox.desktop")

        # Then
        assert info is not None
        assert info.wm_class == "Firefox"
        assert info.icon_name == "firefox"
        assert info.name == "Firefox"

    def test_resolve_falls_back_to_filename_and_executable_name(self, tmp_path):
        # Given
        apps_dir = tmp_path / "applications"
        apps_dir.mkdir()
        desktop_file = apps_dir / "code.desktop"
        desktop_file.write_text("[Desktop Entry]\nName=Code\n")

        launcher = Launcher()
        launcher._desktop_dirs = [apps_dir]

        app = MagicMock()
        app.get_startup_wm_class.return_value = ""
        app.get_commandline.return_value = "/usr/bin/code %F"
        app.get_icon.return_value = None
        app.get_display_name.return_value = ""

        # When
        with (
            patch(
                "docking.platform.launcher.Gio.DesktopAppInfo.new",
                return_value=None,
            ),
            patch(
                "docking.platform.launcher.Gio.DesktopAppInfo.new_from_filename",
                return_value=app,
            ),
        ):
            info = launcher.resolve("code.desktop")

        # Then
        assert info is not None
        assert info.wm_class == "code"
        assert info.icon_name == "application-x-executable"
        assert info.name == "code.desktop"

    def test_resolve_returns_none_when_lookups_fail(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)
        launcher = Launcher()
        launcher._desktop_dirs = []

        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new",
            side_effect=TypeError,
        ):
            info = launcher.resolve("missing.desktop")

        # Then
        assert info is None


class TestTryLoadIcon:
    def test_loads_icon_from_absolute_path(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        theme = MagicMock()
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )
        monkeypatch.setattr(launcher_mod.os.path, "isabs", lambda p: True)
        monkeypatch.setattr(launcher_mod.os.path, "exists", lambda p: True)

        pix = object()
        pixbuf_cls = MagicMock()
        pixbuf_cls.new_from_file_at_scale.return_value = pix
        monkeypatch.setattr(launcher_mod.GdkPixbuf, "Pixbuf", pixbuf_cls, raising=False)

        # When
        out = launcher._try_load_icon("/tmp/icon.png", 48)

        # Then
        assert out is pix
        theme.load_icon.assert_not_called()

    def test_uses_theme_fallback_when_primary_icon_lookup_fails(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)

        theme = MagicMock()
        theme.load_icon.side_effect = [RuntimeError("miss"), "fallback-pixbuf"]
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )
        monkeypatch.setattr(launcher_mod.os.path, "isabs", lambda p: False)

        # When
        out = launcher._try_load_icon("missing-icon", 48)

        # Then
        assert out == "fallback-pixbuf"
        assert theme.load_icon.call_count == 2

    def test_returns_none_when_all_icon_lookups_fail(self, monkeypatch):
        # Given
        from docking.platform import launcher as launcher_mod

        launcher = Launcher()
        monkeypatch.setattr(launcher_mod.GLib, "Error", RuntimeError, raising=False)

        theme = MagicMock()
        theme.load_icon.side_effect = [RuntimeError("miss"), RuntimeError("miss")]
        monkeypatch.setattr(
            launcher_mod.Gtk.IconTheme, "get_default", lambda: theme, raising=False
        )
        monkeypatch.setattr(launcher_mod.os.path, "isabs", lambda p: False)

        # When
        out = launcher._try_load_icon("missing-icon", 48)

        # Then
        assert out is None


class TestLaunch:
    @patch("subprocess.Popen")
    def test_launch_uses_shell_false_and_new_session(self, popen_mock):
        mock_app = MagicMock()
        mock_app.get_commandline.return_value = 'firefox --new-window "%u"'
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch(desktop_id="firefox.desktop")

        popen_mock.assert_called_once()
        args, kwargs = popen_mock.call_args
        assert args[0] == ["firefox", "--new-window"]
        assert kwargs["shell"] is False
        assert kwargs["start_new_session"] is True

    @patch("subprocess.Popen")
    def test_launch_returns_when_desktop_missing(self, popen_mock):
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=None
        ):
            launch(desktop_id="missing.desktop")
        popen_mock.assert_not_called()

    @patch("subprocess.Popen")
    def test_launch_returns_when_commandline_missing(self, popen_mock):
        mock_app = MagicMock()
        mock_app.get_commandline.return_value = ""
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch(desktop_id="foo.desktop")
        popen_mock.assert_not_called()

    @patch("subprocess.Popen")
    def test_launch_handles_bad_exec_parse(self, popen_mock, caplog):
        mock_app = MagicMock()
        mock_app.get_commandline.return_value = 'foo "unterminated'
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch(desktop_id="bad.desktop")
        popen_mock.assert_not_called()
        assert "Failed to parse launch command for bad.desktop" in caplog.text

    @patch("subprocess.Popen")
    def test_launch_returns_when_command_becomes_empty_after_field_codes(
        self, popen_mock
    ):
        # Given
        mock_app = MagicMock()
        mock_app.get_commandline.return_value = "%U"
        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch(desktop_id="empty.desktop")
        # Then
        popen_mock.assert_not_called()

    @patch("subprocess.Popen", side_effect=OSError("boom"))
    def test_launch_prints_when_spawn_fails(self, _popen_mock, caplog):
        # Given
        mock_app = MagicMock()
        mock_app.get_commandline.return_value = "firefox"
        # When
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new",
            return_value=mock_app,
        ):
            launch(desktop_id="firefox.desktop")
        # Then
        assert "Failed to launch firefox.desktop" in caplog.text
