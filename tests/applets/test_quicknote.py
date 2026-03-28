"""Tests for the quick note applet."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

try:
    import gi  # noqa: F401
except ModuleNotFoundError:
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.applets.quicknote.applet as quicknote_applet_mod
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
        from docking.applets.quicknote.applet import QuickNoteApplet

        applet = QuickNoteApplet(48)
        assert applet._note == ""
        assert applet.item.name == "Empty note"

    def test_loads_note_from_prefs(self):
        """Given config with saved note, applet loads it."""
        from docking.applets.quicknote.applet import QuickNoteApplet

        config = MagicMock()
        config.applet_prefs = {"quicknote": {"note": "Remember this"}}
        applet = QuickNoteApplet(48, config=config)
        assert applet._note == "Remember this"
        assert applet.item.name == "Remember this"

    def test_clear_note(self):
        """Given a note, clear resets to empty."""
        from docking.applets.quicknote.applet import QuickNoteApplet

        config = MagicMock()
        config.applet_prefs = {"quicknote": {"note": "Some text"}}
        applet = QuickNoteApplet(48, config=config)
        applet._clear_note()
        assert applet._note == ""

    def test_icon_renders(self):
        """Given an applet, create_icon returns pixbuf."""
        from docking.applets.quicknote.applet import QuickNoteApplet

        applet = QuickNoteApplet(48)
        pixbuf = applet.create_icon(size=48)
        assert pixbuf is not None

    def test_get_menu_items(self):
        """Given an applet, menu has edit and clear items."""
        from docking.applets.quicknote.applet import QuickNoteApplet

        applet = QuickNoteApplet(48)
        items = applet.get_menu_items()
        assert len(items) == 2

    def test_on_clicked_opens_edit_dialog(self, monkeypatch):
        from docking.applets.quicknote.applet import QuickNoteApplet

        applet = QuickNoteApplet(48)
        show = MagicMock()
        monkeypatch.setattr(applet, "_show_edit_dialog", show)

        applet.on_clicked()

        show.assert_called_once_with()

    def test_menu_items_trigger_edit_and_clear_actions(self, monkeypatch):
        from docking.applets.quicknote.applet import QuickNoteApplet

        class _FakeMenuItem:
            def __init__(self, label: str = "") -> None:
                self._signals: dict[str, object] = {}

            def connect(self, signal: str, callback) -> None:
                self._signals[signal] = callback

            def emit(self, signal: str) -> None:
                self._signals[signal](self)

        monkeypatch.setattr(quicknote_applet_mod.Gtk, "MenuItem", _FakeMenuItem)

        applet = QuickNoteApplet(48)
        show = MagicMock()
        clear = MagicMock()
        monkeypatch.setattr(applet, "_show_edit_dialog", show)
        monkeypatch.setattr(applet, "_clear_note", clear)

        items = applet.get_menu_items()
        items[0].emit("activate")
        items[1].emit("activate")

        show.assert_called_once_with()
        clear.assert_called_once_with()

    def test_show_edit_dialog_saves_buffer_contents_on_response(self, monkeypatch):
        from docking.applets.quicknote.applet import QuickNoteApplet

        class _FakeBuffer:
            def __init__(self):
                self._text = ""

            def set_text(self, text: str) -> None:
                self._text = text

            def get_bounds(self):
                return (0, len(self._text))

            def get_text(self, _start, _end, include_hidden_chars=True):
                _ = include_hidden_chars
                return self._text

        class _FakeTextView:
            def __init__(self):
                self.buffer = _FakeBuffer()
                self.focused = False

            def set_wrap_mode(self, _mode):
                return

            def get_buffer(self):
                return self.buffer

            def grab_focus(self):
                self.focused = True

        class _FakeScroll:
            def set_policy(self, *_args):
                return

            def set_vexpand(self, _value):
                return

            def add(self, child):
                created["text_view"] = child

        class _FakeBox:
            def set_spacing(self, _value):
                return

            def set_margin_start(self, _value):
                return

            def set_margin_end(self, _value):
                return

            def set_margin_top(self, _value):
                return

            def set_margin_bottom(self, _value):
                return

            def pack_start(self, *_args):
                return

        class _FakeDialog:
            def __init__(self, **_kwargs):
                self.content = _FakeBox()
                self.response_cb = None

            def set_default_size(self, *_args):
                return

            def set_position(self, *_args):
                return

            def add_button(self, *_args):
                return

            def get_content_area(self):
                return self.content

            def connect(self, signal: str, callback) -> None:
                if signal == "response":
                    self.response_cb = callback

            def show_all(self):
                return

            def destroy(self):
                destroyed.append(True)

        created: dict[str, object] = {}
        destroyed: list[bool] = []
        dialog = _FakeDialog()
        monkeypatch.setattr(quicknote_applet_mod.Gtk, "Dialog", lambda **kwargs: dialog)
        monkeypatch.setattr(quicknote_applet_mod.Gtk, "ScrolledWindow", _FakeScroll)
        monkeypatch.setattr(quicknote_applet_mod.Gtk, "TextView", _FakeTextView)

        config = SimpleNamespace(applet_prefs={}, save=MagicMock())
        applet = QuickNoteApplet(48, config=config)
        applet._note = "old"

        applet._show_edit_dialog()
        text_view = created["text_view"]
        text_view.get_buffer().set_text("updated note")
        dialog.response_cb(dialog, quicknote_applet_mod.Gtk.ResponseType.OK)

        assert applet._note == "updated note"
        assert config.applet_prefs[applet.id]["note"] == "updated note"
        config.save.assert_called_once_with()
        assert destroyed == [True]
