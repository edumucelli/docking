"""Tests for window tracker WM_CLASS matching."""

import sys
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.platform.launcher import DESKTOP_SUFFIX, GNOME_APP_PREFIX  # noqa: E402
from docking.platform.window_tracker import _wm_class_desktop_candidates  # noqa: E402


class TestWmClassCandidates:
    """Desktop ID candidates from WM_CLASS with spaces."""

    def test_no_spaces(self):
        assert _wm_class_desktop_candidates("firefox") == ["firefox"]

    def test_spaces_to_hyphens_and_joined(self):
        result = _wm_class_desktop_candidates("mongodb compass")
        assert "mongodb compass" in result
        assert "mongodb-compass" in result
        assert "mongodbcompass" in result

    def test_multi_word(self):
        result = _wm_class_desktop_candidates("aws vpn client")
        assert "aws-vpn-client" in result
        assert "awsvpnclient" in result

    def test_no_duplicates(self):
        result = _wm_class_desktop_candidates("simple")
        assert len(result) == len(set(result))


class TestDesktopConstants:
    def test_desktop_suffix(self):
        assert DESKTOP_SUFFIX == ".desktop"

    def test_gnome_app_prefix(self):
        # Used to strip GNOME app ID prefixes from desktop filenames
        assert isinstance(GNOME_APP_PREFIX, str)
