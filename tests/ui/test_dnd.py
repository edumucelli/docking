"""Tests for drag-and-drop URI parsing."""

import sys
from unittest.mock import MagicMock

# Mock gi before importing dnd
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.ui.dnd import DRAG_ICON_SCALE, DnDHandler  # noqa: E402


class TestConstants:
    def test_drag_icon_scale_reasonable(self):
        # Given / When / Then
        assert DRAG_ICON_SCALE > 1.0
        assert DRAG_ICON_SCALE < 2.0


class TestPoofAsset:
    def test_poof_svg_exists(self):
        # Given
        from pathlib import Path

        svg = Path(__file__).parent.parent.parent / "docking" / "assets" / "poof.svg"
        # When / Then
        assert svg.exists(), "poof.svg sprite sheet missing"

    def test_poof_svg_path_matches_poof_module(self):
        """Asset path resolved in tests matches the path used in poof.py."""
        # Given
        from pathlib import Path

        import docking.ui.poof as poof_mod

        poof_py = Path(poof_mod.__file__)
        # When
        expected = (poof_py.parent.parent / "assets" / "poof.svg").resolve()
        test_path = (
            Path(__file__).parent.parent.parent / "docking" / "assets" / "poof.svg"
        ).resolve()
        # Then
        assert expected == test_path

    def test_poof_svg_dimensions(self):
        """SVG should be square-width with multiple frames stacked vertically."""
        # Given
        from pathlib import Path

        svg = Path(__file__).parent.parent.parent / "docking" / "assets" / "poof.svg"
        # When
        with open(svg) as f:
            content = f.read(500)
        import re

        match = re.search(r'viewBox="0 0 (\d+) (\d+)"', content)
        # Then
        assert match, "poof.svg missing viewBox"
        w, h = int(match.group(1)), int(match.group(2))
        assert w > 0
        assert h > w, "sprite sheet should be taller than wide (stacked frames)"
        assert h % w == 0, "height should be exact multiple of width"
        assert h // w >= 4, "should have at least 4 frames"


class TestAppletUriRejection:
    """Applet URIs (e.g. from Plank) must not be accepted as desktop files.

    Previously, dropping a Plank applet would open a gap that never closed
    because the URI failed to resolve but drop_insert_index was never cleared.
    """

    def test_applet_uri_returns_none(self):
        # Given -- Plank applet URI
        uri = "applet://clock"
        # When / Then
        assert DnDHandler._uri_to_desktop_id(uri) is None

    def test_applet_uri_cpumonitor(self):
        assert DnDHandler._uri_to_desktop_id("applet://cpumonitor") is None

    def test_applet_uri_trash(self):
        assert DnDHandler._uri_to_desktop_id("applet://trash") is None


class TestUriToDesktopId:
    def test_file_uri(self):
        # Given
        uri = "file:///usr/share/applications/firefox.desktop"
        # When / Then
        assert DnDHandler._uri_to_desktop_id(uri) == "firefox.desktop"

    def test_file_uri_with_spaces(self):
        # Given
        uri = "file:///usr/share/applications/my%20app.desktop"
        # When / Then
        assert DnDHandler._uri_to_desktop_id(uri) == "my app.desktop"

    def test_file_uri_snap(self):
        # Given
        uri = "file:///var/lib/snapd/desktop/applications/firefox_firefox.desktop"
        # When / Then
        assert DnDHandler._uri_to_desktop_id(uri) == "firefox_firefox.desktop"

    def test_plain_path(self):
        # Given
        uri = "firefox.desktop"
        # When / Then
        assert DnDHandler._uri_to_desktop_id(uri) == "firefox.desktop"

    def test_non_desktop_file_returns_none(self):
        # Given
        uri = "file:///home/user/document.pdf"
        # When / Then
        assert DnDHandler._uri_to_desktop_id(uri) is None

    def test_http_uri_returns_none(self):
        # Given
        uri = "https://example.com/app.desktop"
        # When / Then
        assert DnDHandler._uri_to_desktop_id(uri) is None

    def test_empty_string_returns_none(self):
        # Given / When / Then
        assert DnDHandler._uri_to_desktop_id("") is None

    def test_non_desktop_plain_path(self):
        # Given / When / Then
        assert DnDHandler._uri_to_desktop_id("readme.txt") is None
