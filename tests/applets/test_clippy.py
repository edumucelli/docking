"""Tests for the Clippy clipboard history applet."""

from docking.applets.clippy import ClippyApplet, _truncate


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
    def test_add_clip(self):
        d = ClippyApplet(48)
        d.add_clip("first")
        d.add_clip("second")
        assert d._clips == ["first", "second"]

    def test_dedup_moves_to_end(self):
        d = ClippyApplet(48)
        d.add_clip("a")
        d.add_clip("b")
        d.add_clip("a")
        assert d._clips == ["b", "a"]

    def test_cap_at_max_entries(self):
        d = ClippyApplet(48)
        d._max_entries = 3
        for i in range(5):
            d.add_clip(str(i))
        assert len(d._clips) == 3
        assert d._clips == ["2", "3", "4"]

    def test_position_tracks_newest(self):
        d = ClippyApplet(48)
        d.add_clip("a")
        assert d._cur_position == 1
        d.add_clip("b")
        assert d._cur_position == 2


class TestClipScroll:
    def test_scroll_up_decrements(self):
        d = ClippyApplet(48)
        d.add_clip("a")
        d.add_clip("b")
        d.add_clip("c")
        assert d._cur_position == 3
        d.on_scroll(direction_up=True)
        assert d._cur_position == 2

    def test_scroll_wraps_around(self):
        d = ClippyApplet(48)
        d.add_clip("a")
        d.add_clip("b")
        d._cur_position = 1
        d.on_scroll(direction_up=True)
        assert d._cur_position == 2  # wraps to end

    def test_scroll_down_increments(self):
        d = ClippyApplet(48)
        d.add_clip("a")
        d.add_clip("b")
        d._cur_position = 1
        d.on_scroll(direction_up=False)
        assert d._cur_position == 2

    def test_scroll_empty_noop(self):
        d = ClippyApplet(48)
        d.on_scroll(direction_up=True)  # no crash
        assert d._cur_position == 0


class TestClipMenu:
    def test_empty_returns_empty(self):
        d = ClippyApplet(48)
        assert d.get_menu_items() == []

    def test_returns_clips_newest_first(self):
        d = ClippyApplet(48)
        d.add_clip("old")
        d.add_clip("new")
        items = d.get_menu_items()
        # 2 clips + separator + clear = 4 items
        assert len(items) == 4
        assert items[0].get_label() == "new"
        assert items[1].get_label() == "old"

    def test_clear_empties_list(self):
        d = ClippyApplet(48)
        d.add_clip("text")
        d._clear()
        assert d._clips == []
        assert d._cur_position == 0


class TestClipRendering:
    def test_creates_with_icon(self):
        d = ClippyApplet(48)
        assert d.item.icon is not None

    def test_tooltip_empty(self):
        d = ClippyApplet(48)
        d.create_icon(48)
        assert "empty" in d.item.name.lower()

    def test_tooltip_shows_current_clip(self):
        d = ClippyApplet(48)
        d.add_clip("hello world")
        d.create_icon(48)
        assert "hello world" in d.item.name

    def test_tooltip_updates_on_scroll(self):
        # Given two clips
        d = ClippyApplet(48)
        d.add_clip("first")
        d.add_clip("second")
        d.create_icon(48)
        assert "second" in d.item.name

        # When scroll up (to older clip)
        d.on_scroll(direction_up=True)

        # Then tooltip reflects older clip immediately
        assert "first" in d.item.name

    def test_scroll_down_wraps_and_updates_tooltip(self):
        d = ClippyApplet(48)
        d.add_clip("a")
        d.add_clip("b")
        d._cur_position = 2  # at "b"
        d.on_scroll(direction_up=False)  # wraps to "a"
        assert d._cur_position == 1
        # create_icon is called by refresh_icon inside on_scroll
        # so item.name should already be updated
        assert "a" in d.item.name
