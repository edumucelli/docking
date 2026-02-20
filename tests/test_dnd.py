"""Tests for drag-and-drop URI parsing."""

import sys
import pytest
from unittest.mock import MagicMock

# Mock gi before importing dnd
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.dnd import DnDHandler  # noqa: E402


class TestUriToDesktopId:
    def test_file_uri(self):
        uri = "file:///usr/share/applications/firefox.desktop"
        assert DnDHandler._uri_to_desktop_id(uri) == "firefox.desktop"

    def test_file_uri_with_spaces(self):
        uri = "file:///usr/share/applications/my%20app.desktop"
        assert DnDHandler._uri_to_desktop_id(uri) == "my app.desktop"

    def test_file_uri_snap(self):
        uri = "file:///var/lib/snapd/desktop/applications/firefox_firefox.desktop"
        assert DnDHandler._uri_to_desktop_id(uri) == "firefox_firefox.desktop"

    def test_plain_path(self):
        uri = "firefox.desktop"
        assert DnDHandler._uri_to_desktop_id(uri) == "firefox.desktop"

    def test_non_desktop_file_returns_none(self):
        uri = "file:///home/user/document.pdf"
        assert DnDHandler._uri_to_desktop_id(uri) is None

    def test_http_uri_returns_none(self):
        uri = "https://example.com/app.desktop"
        assert DnDHandler._uri_to_desktop_id(uri) is None

    def test_empty_string_returns_none(self):
        assert DnDHandler._uri_to_desktop_id("") is None

    def test_non_desktop_plain_path(self):
        assert DnDHandler._uri_to_desktop_id("readme.txt") is None
