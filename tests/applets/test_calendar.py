"""Tests for the calendar applet."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import cairo
import pytest

import docking.applets.calendar.applet as calendar_applet_mod
from docking.applets.calendar.applet import CalendarApplet
from docking.applets.calendar.render import _render_calendar_icon


class TestRenderCalendarIcon:
    """_render_calendar_icon should draw a non-empty icon for any valid day."""

    @pytest.mark.parametrize("day", [1, 15, 28, 31])
    def test_renders_without_error(self, day):
        size = 48
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        # Given a valid day and weekday
        _render_calendar_icon(cr=cr, size=size, day=day, weekday="Mon")
        # Then no exception and surface has content
        data = surface.get_data()
        assert any(b != 0 for b in data)

    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_renders_at_various_sizes(self, size):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_calendar_icon(cr=cr, size=size, day=25, weekday="Tue")
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

    def test_start_and_stop_manage_timer(self, monkeypatch):
        # Given
        applet = CalendarApplet(48)
        remove = MagicMock()
        monkeypatch.setattr(
            calendar_applet_mod.GLib, "timeout_add_seconds", lambda *_a: 77
        )
        monkeypatch.setattr(calendar_applet_mod.GLib, "source_remove", remove)

        # When
        applet.start(notify=lambda: None)
        applet.stop()

        # Then
        assert applet._timer_id == 0
        remove.assert_called_once_with(77)

    def test_tick_refreshes_presentation_when_day_changes(self, monkeypatch):
        # Given
        applet = CalendarApplet(48)
        applet._last_day = 10
        applet.present = MagicMock()
        monkeypatch.setattr(
            calendar_applet_mod,
            "snapshot_from",
            lambda: SimpleNamespace(day=11, tooltip="Tue, Jan 11"),
        )

        # When
        result = applet._tick()

        # Then
        assert result is True
        applet.present.assert_called_once()
        assert "Jan 11" in applet._tooltip_text

    def test_tick_updates_tooltip_only_when_same_day(self, monkeypatch):
        # Given
        applet = CalendarApplet(48)
        applet._last_day = 10
        applet.present = MagicMock()
        monkeypatch.setattr(
            calendar_applet_mod,
            "snapshot_from",
            lambda: SimpleNamespace(day=10, tooltip="Mon, Jan 10"),
        )

        # When
        result = applet._tick()

        # Then
        assert result is True
        applet.present.assert_not_called()
        assert applet.item.name == "Mon, Jan 10"

    def test_on_clicked_hides_existing_popup(self):
        # Given
        applet = CalendarApplet(48)
        applet._popup = MagicMock()
        applet._popup.get_visible.return_value = True
        applet._show_popup = MagicMock()

        # When
        applet.on_clicked()

        # Then
        applet._popup.hide.assert_called_once()
        applet._show_popup.assert_not_called()


class _FakeChild:
    pass


class _FakeCalendar:
    def set_margin_start(self, _value: int) -> None:
        return

    def set_margin_end(self, _value: int) -> None:
        return

    def set_margin_top(self, _value: int) -> None:
        return

    def set_margin_bottom(self, _value: int) -> None:
        return


class _FakePopupScreen:
    def __init__(self, width: int = 500, height: int = 320) -> None:
        self._width = width
        self._height = height

    def get_rgba_visual(self):
        return object()

    def get_width(self) -> int:
        return self._width

    def get_height(self) -> int:
        return self._height


class _FakePopupWindow:
    def __init__(self, **_kwargs) -> None:
        self._child = _FakeChild()
        self._screen = _FakePopupScreen()
        self.move = MagicMock()
        self.show_all = MagicMock()
        self.connect = MagicMock()
        self.remove = MagicMock(side_effect=self._remove)
        self.add = MagicMock(side_effect=self._add)
        self.destroy = MagicMock()
        self.set_decorated = MagicMock()
        self.set_skip_taskbar_hint = MagicMock()
        self.set_type_hint = MagicMock()
        self.set_app_paintable = MagicMock()
        self.set_visual = MagicMock()

    def _remove(self, _child) -> None:
        self._child = None

    def _add(self, child) -> None:
        self._child = child

    def get_child(self):
        return self._child

    def get_screen(self):
        return self._screen

    def get_preferred_size(self):
        return (
            SimpleNamespace(width=0, height=0),
            SimpleNamespace(width=200, height=130),
        )


class TestCalendarPopup:
    def test_show_popup_creates_window_and_clamps_position(self, monkeypatch):
        # Given
        applet = CalendarApplet(48)
        fake_popup = _FakePopupWindow()
        pointer = MagicMock()
        pointer.get_position.return_value = (None, 20, 15)
        seat = MagicMock()
        seat.get_pointer.return_value = pointer
        display = MagicMock()
        display.get_default_seat.return_value = seat

        monkeypatch.setattr(
            calendar_applet_mod,
            "Gtk",
            SimpleNamespace(
                Window=lambda **_kwargs: fake_popup,
                WindowType=SimpleNamespace(POPUP=1),
                Calendar=_FakeCalendar,
                Widget=object,
            ),
        )
        monkeypatch.setattr(
            calendar_applet_mod.Gdk.Display,
            "get_default",
            lambda: display,
        )

        # When
        applet._show_popup()

        # Then
        assert applet._popup is fake_popup
        fake_popup.remove.assert_called_once()
        fake_popup.add.assert_called_once()
        fake_popup.show_all.assert_called_once()
        fake_popup.move.assert_called_once_with(0, 0)

    def test_stop_destroys_popup(self):
        # Given
        applet = CalendarApplet(48)
        popup = MagicMock()
        applet._popup = popup

        # When
        applet.stop()

        # Then
        popup.destroy.assert_called_once()
        assert applet._popup is None
