"""Tests for poof animation constants and asset."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.ui.poof import POOF_DURATION_MS  # noqa: E402


class TestPoofConstants:
    def test_duration_reasonable(self):
        # Short enough to feel snappy, long enough to be visible
        assert 100 <= POOF_DURATION_MS <= 500

    def test_poof_svg_exists(self):
        svg = (
            Path(__file__).resolve().parent.parent.parent
            / "docking"
            / "assets"
            / "poof.svg"
        )
        assert svg.exists()
