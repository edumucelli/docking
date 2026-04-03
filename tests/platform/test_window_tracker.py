"""Tests for window tracker WM_CLASS matching."""

import sys
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.platform.launcher import DESKTOP_SUFFIX, GNOME_APP_PREFIX
from docking.platform.window_tracker import _wm_class_desktop_candidates


class TestWmClassCandidates:
    """Desktop ID candidates from WM_CLASS with spaces."""

    def test_no_spaces(self):
        assert _wm_class_desktop_candidates(
            class_lower="firefox",
            class_group="Firefox",
        ) == ["firefox", f"{GNOME_APP_PREFIX}Firefox"]

    def test_spaces_to_hyphens_and_joined(self):
        result = _wm_class_desktop_candidates(
            class_lower="mongodb compass",
            class_group="MongoDB Compass",
        )
        assert "mongodb compass" in result
        assert "mongodb-compass" in result
        assert "mongodbcompass" in result
        assert f"{GNOME_APP_PREFIX}MongoDB Compass" in result

    def test_multi_word(self):
        result = _wm_class_desktop_candidates(
            class_lower="aws vpn client",
            class_group="AWS VPN Client",
        )
        assert "aws-vpn-client" in result
        assert "awsvpnclient" in result

    def test_no_duplicates(self):
        result = _wm_class_desktop_candidates(
            class_lower="simple",
            class_group="simple",
        )
        assert len(result) == len(set(result))


class TestDesktopConstants:
    def test_desktop_suffix(self):
        assert DESKTOP_SUFFIX == ".desktop"

    def test_gnome_app_prefix(self):
        # Used to strip GNOME app ID prefixes from desktop filenames
        assert isinstance(GNOME_APP_PREFIX, str)
