"""Tests for the Clippy clipboard history applet."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import docking.applets.clippy.applet as clippy_mod
from docking.applets.clippy.applet import ClippyApplet
from docking.applets.clippy.state import (
    DEFAULT_MAX_ENTRIES,
    MAX_MAX_ENTRIES,
    _truncate,
    add_clip,
    normalize_max_entries,
)
from docking.core.config import Config


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_long_text_truncated(self):
        result = _truncate("a" * 60)
        assert len(result) == 53  # 50 + "..."
        assert result.endswith("...")

    def test_newlines_replaced(self):
        assert _truncate("line1\nline2\ttab") == "line1 line2 tab"

    def test_strips_whitespace(self):
        assert _truncate("  hello  ") == "hello"


class TestClipHistory:
    @pytest.mark.parametrize("cap", [0, "0", 0.0])
    def test_zero_cap_does_not_retain_clipboard_text(self, cap):
        applet = ClippyApplet(
            48, config=Config(applet_prefs={"clippy": {"max_entries": cap}})
        )
        clipboard = MagicMock()
        clipboard.wait_for_text.side_effect = ["private text", ""]
        applet._clipboard = clipboard

        applet._on_owner_change(clipboard, None)
        applet._on_owner_change(clipboard, None)
        applet.on_clicked()

        assert applet._clips == []
        assert applet._cur_position == 0
        assert "empty" in applet.item.name.lower()
        assert applet.get_menu_items() == []
        clipboard.set_text.assert_not_called()

    def test_add_clip(self):
        d = ClippyApplet(48, config=Config())
        d.add_clip("first")
        d.add_clip("second")
        assert d._clips == ["first", "second"]

    def test_dedup_moves_to_end(self):
        d = ClippyApplet(48, config=Config())
        d.add_clip("a")
        d.add_clip("b")
        d.add_clip("a")
        assert d._clips == ["b", "a"]

    def test_cap_at_max_entries(self):
        d = ClippyApplet(48, config=Config())
        d._max_entries = 3
        for i in range(5):
            d.add_clip(str(i))
        assert len(d._clips) == 3
        assert d._clips == ["2", "3", "4"]

    def test_position_tracks_newest(self):
        d = ClippyApplet(48, config=Config())
        d.add_clip("a")
        assert d._cur_position == 1
        d.add_clip("b")
        assert d._cur_position == 2


class TestClipScroll:
    def test_scroll_up_decrements(self):
        d = ClippyApplet(48, config=Config())
        d.add_clip("a")
        d.add_clip("b")
        d.add_clip("c")
        assert d._cur_position == 3
        d.on_scroll(direction_up=True)
        assert d._cur_position == 2

    def test_scroll_wraps_around(self):
        d = ClippyApplet(48, config=Config())
        d.add_clip("a")
        d.add_clip("b")
        d._cur_position = 1
        d.on_scroll(direction_up=True)
        assert d._cur_position == 2  # wraps to end

    def test_scroll_down_increments(self):
        d = ClippyApplet(48, config=Config())
        d.add_clip("a")
        d.add_clip("b")
        d._cur_position = 1
        d.on_scroll(direction_up=False)
        assert d._cur_position == 2

    def test_scroll_empty_noop(self):
        d = ClippyApplet(48, config=Config())
        d.on_scroll(direction_up=True)  # no crash
        assert d._cur_position == 0


class TestClipMenu:
    def _fake_gtk(self, monkeypatch):
        class _FakeMenuItem:
            def __init__(self, label: str = "") -> None:
                self._label = label
                self._signals: dict[str, list[object]] = {}
                self._sensitive = True

            def get_label(self) -> str:
                return self._label

            def connect(self, signal: str, callback) -> None:
                self._signals.setdefault(signal, []).append(callback)

            def set_sensitive(self, value: bool) -> None:
                self._sensitive = value

            def get_sensitive(self) -> bool:
                return self._sensitive

        class _FakeSeparatorMenuItem(_FakeMenuItem):
            def __init__(self) -> None:
                super().__init__(label="")

        monkeypatch.setattr(
            clippy_mod,
            "Gtk",
            SimpleNamespace(
                MenuItem=_FakeMenuItem,
                SeparatorMenuItem=_FakeSeparatorMenuItem,
            ),
        )

    def test_empty_returns_empty(self):
        d = ClippyApplet(48, config=Config())
        assert d.get_menu_items() == []

    def test_returns_clips_newest_first(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        d = ClippyApplet(48, config=Config())
        d.add_clip("old")
        d.add_clip("new")
        items = d.get_menu_items()
        # 2 clips + separator + clear = 4 items
        assert len(items) == 4
        assert items[0].get_label() == "new"
        assert items[1].get_label() == "old"

    def test_clear_empties_list(self):
        d = ClippyApplet(48, config=Config())
        d.add_clip("text")
        d._clear()
        assert d._clips == []
        assert d._cur_position == 0


class TestClipRendering:
    def test_creates_with_icon(self):
        d = ClippyApplet(48, config=Config())
        assert d.item.icon is not None

    def test_tooltip_empty(self):
        d = ClippyApplet(48, config=Config())
        d.create_icon(48)
        assert "empty" in d.item.name.lower()

    def test_tooltip_shows_current_clip(self):
        d = ClippyApplet(48, config=Config())
        d.add_clip("hello world")
        d.refresh_tooltip()
        assert "hello world" in d.item.name

    def test_tooltip_updates_on_scroll(self):
        # Given two clips
        d = ClippyApplet(48, config=Config())
        d.add_clip("first")
        d.add_clip("second")
        d.refresh_tooltip()
        assert "second" in d.item.name

        # When scroll up (to older clip)
        d.on_scroll(direction_up=True)

        # Then tooltip reflects older clip immediately
        assert "first" in d.item.name

    def test_scroll_down_wraps_and_updates_tooltip(self):
        d = ClippyApplet(48, config=Config())
        d.add_clip("a")
        d.add_clip("b")
        d._cur_position = 2  # at "b"
        d.on_scroll(direction_up=False)  # wraps to "a"
        assert d._cur_position == 1
        # create_icon is called by present inside on_scroll
        # so item.name should already be updated
        assert "a" in d.item.name


class TestClipLifecycle:
    def test_loads_max_entries_from_config(self):
        applet = ClippyApplet(
            48, config=Config(applet_prefs={"clippy": {"max_entries": 7}})
        )
        assert applet._max_entries == 7

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            (-1, 1),  # used to raise IndexError on every single copy
            (0, 0),
            ("15", 15),  # used to raise TypeError on every copy
            ("not a number", DEFAULT_MAX_ENTRIES),
            (None, DEFAULT_MAX_ENTRIES),
            (float("nan"), DEFAULT_MAX_ENTRIES),
            (float("inf"), DEFAULT_MAX_ENTRIES),
            (True, DEFAULT_MAX_ENTRIES),
            (10**9, MAX_MAX_ENTRIES),
        ],
    )
    def test_normalize_max_entries_survives_config_file_values(self, stored, expected):
        """A raw cap left the applet unable to record a single clipboard change."""
        assert normalize_max_entries(stored) == expected

    def test_negative_persisted_cap_still_records_clips(self):
        applet = ClippyApplet(
            48, config=Config(applet_prefs={"clippy": {"max_entries": -1}})
        )

        applet.add_clip("first")
        applet.add_clip("second")

        assert applet._clips == ["second"]

    def test_add_clip_tolerates_a_zero_cap(self):
        """The pop loop must stop at the empty list whatever the cap is."""
        assert add_clip(clips=[], text="hi", max_entries=-5) == ([], 0)

    def test_on_clicked_copies_current_clip_when_available(self):
        applet = ClippyApplet(48, config=Config())
        applet._clips = ["a", "b"]
        applet._cur_position = 2
        applet._clipboard = MagicMock()

        applet.on_clicked()

        applet._clipboard.set_text.assert_called_once_with("b", -1)
        applet._clipboard.store.assert_called_once()

    def test_start_connects_clipboard_and_stop_disconnects(self, monkeypatch):
        applet = ClippyApplet(48, config=Config())
        clipboard = MagicMock()
        clipboard.connect.return_value = 99
        monkeypatch.setattr(
            clippy_mod.Gtk.Clipboard, "get", lambda *_a, **_k: clipboard
        )

        applet.start(lambda: None)
        assert applet._clipboard is clipboard
        assert applet._handler_id == 99

        applet.stop()
        clipboard.disconnect.assert_called_once_with(99)
        assert applet._clipboard is None
        assert applet._handler_id == 0

    def test_owner_change_adds_clip_and_refreshes(self):
        applet = ClippyApplet(48, config=Config())
        clipboard = MagicMock()
        clipboard.wait_for_text.return_value = "new text"
        applet.add_clip = MagicMock()
        applet.present = MagicMock()

        applet._on_owner_change(clipboard, None)

        applet.add_clip.assert_called_once_with(text="new text")
        applet.present.assert_called_once()

    def test_copy_to_clipboard_helper_uses_clipboard(self):
        applet = ClippyApplet(48, config=Config())
        applet._clipboard = MagicMock()

        applet._copy_to_clipboard("hello")

        applet._clipboard.set_text.assert_called_once_with("hello", -1)
        applet._clipboard.store.assert_called_once()
