"""Tests for desktop file resolution."""

import sys
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Mock gi before importing launcher
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.platform.launcher import Launcher  # noqa: E402


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
        icon48 = launcher.load_icon("application-x-executable", 48)
        icon96 = launcher.load_icon("application-x-executable", 96)
        # Then — different cache keys
        assert ("application-x-executable", 48) in launcher._icon_cache
        assert ("application-x-executable", 96) in launcher._icon_cache
