"""Tests for window tracker WM_CLASS matching."""

import sys
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.platform.launcher import DESKTOP_SUFFIX, GNOME_APP_PREFIX  # noqa: E402


class TestDesktopConstants:
    def test_desktop_suffix(self):
        assert DESKTOP_SUFFIX == ".desktop"

    def test_gnome_app_prefix(self):
        # Used to strip GNOME app ID prefixes from desktop filenames
        assert isinstance(GNOME_APP_PREFIX, str)
