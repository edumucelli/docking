"""Tests for the calendar applet."""

import time

import cairo
import pytest

from docking.applets.calendar import CalendarApplet, _render_calendar_icon


class TestRenderCalendarIcon:
    """_render_calendar_icon should draw a non-empty icon for any valid day."""

    @pytest.mark.parametrize("day", [1, 15, 28, 31])
    def test_renders_without_error(self, day):
        size = 48
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        # Given a valid day and weekday
        _render_calendar_icon(cr, size, day, "Mon")
        # Then no exception and surface has content
        data = surface.get_data()
        assert any(b != 0 for b in data)

    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_renders_at_various_sizes(self, size):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_calendar_icon(cr, size, 25, "Tue")
        data = surface.get_data()
        assert any(b != 0 for b in data)


class TestCalendarApplet:
    def test_creates_with_icon(self):
        applet = CalendarApplet(48)
        assert applet.item.icon is not None

    def test_tooltip_is_full_date(self):
        applet = CalendarApplet(48)
        # Given a second create_icon call (item exists after __init__)
        applet.create_icon(48)
        # Then tooltip contains current day number
        today = str(time.localtime().tm_mday)
        assert today in applet.item.name

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = CalendarApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_no_menu_items(self):
        applet = CalendarApplet(48)
        assert applet.get_menu_items() == []
