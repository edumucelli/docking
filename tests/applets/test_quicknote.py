"""Tests for the quick note applet."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

try:
    import gi  # noqa: F401
except ModuleNotFoundError:
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.applets.quicknote.state import (
    MAX_TOOLTIP_LEN,
    note_from_prefs,
    prefs_from_note,
    tooltip_text,
)


class TestState:
    """Pure state logic tests."""

    def test_tooltip_text_truncates_long_note(self):
        """Given a note longer than MAX_TOOLTIP_LEN, tooltip is truncated."""
        long_note = "a" * (MAX_TOOLTIP_LEN + 20)
        result = tooltip_text(note=long_note)
        assert result.endswith("...")
        assert len(result) == MAX_TOOLTIP_LEN + 3

    def test_tooltip_text_empty_note(self):
        """Given an empty note, tooltip shows placeholder."""
        assert tooltip_text(note="") == "Empty note"

    def test_tooltip_text_whitespace_only(self):
        """Given whitespace-only note, tooltip shows placeholder."""
        assert tooltip_text(note="   \n  ") == "Empty note"

    def test_tooltip_text_short_note(self):
        """Given a short note, tooltip shows full first line."""
        assert tooltip_text(note="Buy milk") == "Buy milk"

    def test_tooltip_text_multiline_shows_first_line(self):
        """Given a multiline note, tooltip shows only the first line."""
        assert tooltip_text(note="Line 1\nLine 2\nLine 3") == "Line 1"

    def test_prefs_round_trip(self):
        """Given a note, prefs serialization round-trips."""
        note = "Hello, world!"
        prefs = prefs_from_note(note=note)
        assert note_from_prefs(prefs) == note

    def test_note_from_prefs_none(self):
        """Given None prefs, returns empty string."""
        assert note_from_prefs(None) == ""

    def test_note_from_prefs_empty_dict(self):
        """Given empty dict, returns empty string."""
        assert note_from_prefs({}) == ""


class TestRender:
    """Cairo rendering tests."""

    def test_render_icon_with_content(self):
        """Given has_content=True, returns a pixbuf."""
        from docking.applets.quicknote.render import render_icon

        pixbuf = render_icon(size=48, has_content=True)
        assert pixbuf is not None
        assert pixbuf.get_width() == 48

    def test_render_icon_without_content(self):
        """Given has_content=False, returns a pixbuf."""
        from docking.applets.quicknote.render import render_icon

        pixbuf = render_icon(size=48, has_content=False)
        assert pixbuf is not None

    def test_render_icon_various_sizes(self):
        """Given various sizes, all return valid pixbufs."""
        from docking.applets.quicknote.render import render_icon

        for size in [32, 48, 64]:
            pixbuf = render_icon(size=size, has_content=True)
            assert pixbuf is not None
            assert pixbuf.get_width() == size
            assert pixbuf.get_height() == size


class TestApplet:
    """GTK lifecycle tests."""

    def test_creates_with_empty_note(self):
        """Given no config, applet starts with empty note."""
        from docking.applets.quicknote import QuickNoteApplet

        applet = QuickNoteApplet(48)
        assert applet._note == ""
        assert applet.item.name == "Empty note"

    def test_loads_note_from_prefs(self):
        """Given config with saved note, applet loads it."""
        from docking.applets.quicknote import QuickNoteApplet

        config = MagicMock()
        config.applet_prefs = {"quicknote": {"note": "Remember this"}}
        applet = QuickNoteApplet(48, config=config)
        assert applet._note == "Remember this"
        assert applet.item.name == "Remember this"

    def test_clear_note(self):
        """Given a note, clear resets to empty."""
        from docking.applets.quicknote import QuickNoteApplet

        config = MagicMock()
        config.applet_prefs = {"quicknote": {"note": "Some text"}}
        applet = QuickNoteApplet(48, config=config)
        applet._clear_note()
        assert applet._note == ""

    def test_icon_renders(self):
        """Given an applet, create_icon returns pixbuf."""
        from docking.applets.quicknote import QuickNoteApplet

        applet = QuickNoteApplet(48)
        pixbuf = applet.create_icon(size=48)
        assert pixbuf is not None

    def test_get_menu_items(self):
        """Given an applet, menu has edit and clear items."""
        from docking.applets.quicknote import QuickNoteApplet

        applet = QuickNoteApplet(48)
        items = applet.get_menu_items()
        assert len(items) == 2
