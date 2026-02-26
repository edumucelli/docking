"""Tests for desktop file resolution."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Mock gi before importing launcher
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.platform.launcher import Launcher, get_actions, launch_action  # noqa: E402


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
        # Then — both should be the same object (cached)
        assert icon1 is icon2

    def test_different_sizes_cached_separately(self):
        # Given
        launcher = Launcher()
        # When
        launcher.load_icon("application-x-executable", 48)
        launcher.load_icon("application-x-executable", 96)
        # Then — different cache keys
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
            actions = get_actions("chrome.desktop")

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
            actions = get_actions("nonexistent.desktop")
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
            actions = get_actions("app.desktop")
        # Then -- empty-name action skipped
        assert actions == [("good", "Good")]

    def test_launch_action_calls_gio(self):
        # Given
        mock_app = MagicMock()
        with patch(
            "docking.platform.launcher.Gio.DesktopAppInfo.new", return_value=mock_app
        ):
            launch_action("chrome.desktop", "new-window")
        # Then
        mock_app.launch_action.assert_called_once_with("new-window", None)
