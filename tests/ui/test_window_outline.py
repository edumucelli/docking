"""Tests for the click-through window outline overlay."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from docking.platform.backends.base import Rect

gtk_ui = pytest.importorskip("docking.ui.window_outline")


@pytest.fixture(autouse=True)
def composited_screen(monkeypatch):
    """Bare X servers (the CI Xephyr) have no compositor; assume one by default."""
    screen = MagicMock()
    screen.is_composited.return_value = True
    monkeypatch.setattr(
        gtk_ui.Gdk.Screen, "is_composited", lambda _self: screen.is_composited()
    )
    return screen


class TestWindowOutlineHandlers:
    def test_constructor_sets_empty_input_shape(self, monkeypatch):
        shapes = []
        monkeypatch.setattr(
            gtk_ui.WindowOutline,
            "input_shape_combine_region",
            lambda _self, region: shapes.append(region),
        )

        outline = gtk_ui.WindowOutline()
        outline.destroy()

        assert len(shapes) == 1
        assert shapes[0].is_empty()

    def test_draw_strokes_inset_rectangle_border_only(self):
        widget = MagicMock()
        widget.get_allocated_width.return_value = 300
        widget.get_allocated_height.return_value = 200
        cr = MagicMock()

        handled = gtk_ui.WindowOutline._on_draw(widget, cr)

        assert handled is True
        half = gtk_ui.OUTLINE_WIDTH_PX / 2
        cr.rectangle.assert_called_once_with(
            half,
            half,
            300 - gtk_ui.OUTLINE_WIDTH_PX,
            200 - gtk_ui.OUTLINE_WIDTH_PX,
        )
        cr.stroke.assert_called_once()
        cr.fill.assert_not_called()


class TestWindowOutlineWidget:
    def test_show_around_places_and_sizes_window(self):
        outline = gtk_ui.WindowOutline()
        try:
            outline.show_around(Rect(30, 40, 500, 400))
            assert outline.get_visible()
            assert outline.get_size() == (500, 400)
            assert outline.get_position() == (30, 40)
            assert outline.get_accept_focus() is False
            outline.hide()
            assert not outline.get_visible()
        finally:
            outline.destroy()

    def test_show_around_converts_device_pixels_at_scale_two(self, monkeypatch):
        monkeypatch.setattr(gtk_ui.WindowOutline, "get_scale_factor", lambda _self: 2)
        outline = gtk_ui.WindowOutline()
        try:
            outline.show_around(Rect(60, 80, 600, 400))
            assert outline.get_size() == (300, 200)
            assert outline.get_position() == (30, 40)
        finally:
            outline.destroy()

    def test_show_around_keeps_coordinates_at_scale_one(self, monkeypatch):
        monkeypatch.setattr(gtk_ui.WindowOutline, "get_scale_factor", lambda _self: 1)
        outline = gtk_ui.WindowOutline()
        try:
            outline.show_around(Rect(60, 80, 600, 400))
            assert outline.get_size() == (600, 400)
            assert outline.get_position() == (60, 80)
        finally:
            outline.destroy()

    def test_degenerate_rect_stays_hidden(self):
        outline = gtk_ui.WindowOutline()
        try:
            outline.show_around(Rect(0, 0, 0, 10))
            assert not outline.get_visible()
        finally:
            outline.destroy()


class TestWindowOutlineTransparencyGate:
    def test_not_composited_hides_instead_of_showing(self, composited_screen):
        surface = MagicMock()
        surface.overlay_uses_toplevel = False
        outline = gtk_ui.WindowOutline(surface)
        try:
            outline.show_around(Rect(30, 40, 500, 400))
            assert outline.get_visible()
            composited_screen.is_composited.return_value = False
            surface.place_overlay.reset_mock()
            outline.show_around(Rect(30, 40, 500, 400))
            assert not outline.get_visible()
            surface.place_overlay.assert_not_called()
        finally:
            outline.destroy()

    def test_composited_screen_shows(self, composited_screen):
        composited_screen.is_composited.return_value = True
        outline = gtk_ui.WindowOutline()
        try:
            outline.show_around(Rect(30, 40, 500, 400))
            assert outline.get_visible()
        finally:
            outline.destroy()

    def test_missing_rgba_visual_hides_even_when_composited(self):
        outline = gtk_ui.WindowOutline()
        outline._has_rgba_visual = False
        try:
            outline.show_around(Rect(30, 40, 500, 400))
            assert not outline.get_visible()
        finally:
            outline.destroy()

    def test_degenerate_rect_hides_without_consulting_the_screen(
        self, composited_screen
    ):
        outline = gtk_ui.WindowOutline()
        try:
            outline.show_around(Rect(30, 40, 500, 400))
            composited_screen.is_composited.reset_mock()
            outline.show_around(Rect(0, 0, 0, 10))
            assert not outline.get_visible()
            composited_screen.is_composited.assert_not_called()
        finally:
            outline.destroy()


class TestWindowOutlineSurfaceDelegation:
    @staticmethod
    def _surface(*, toplevel: bool) -> MagicMock:
        surface = MagicMock()
        surface.overlay_uses_toplevel = toplevel
        return surface

    def test_prepares_through_the_surface_service_before_realize(self):
        surface = self._surface(toplevel=False)
        outline = gtk_ui.WindowOutline(surface)
        try:
            surface.prepare_overlay_window.assert_called_once_with(outline)
            assert not outline.get_realized()
        finally:
            outline.destroy()

    def test_show_around_delegates_placement_instead_of_moving(self, monkeypatch):
        surface = self._surface(toplevel=False)
        outline = gtk_ui.WindowOutline(surface)
        moves = []
        monkeypatch.setattr(outline, "move", lambda *args: moves.append(args))
        try:
            outline.show_around(Rect(30, 40, 500, 400))
            surface.place_overlay.assert_called_once_with(
                outline, Rect(30, 40, 500, 400)
            )
            assert moves == []
            assert outline.get_visible()
        finally:
            outline.destroy()

    def test_surface_receives_the_rect_untouched_at_scale_two(self, monkeypatch):
        monkeypatch.setattr(gtk_ui.WindowOutline, "get_scale_factor", lambda _self: 2)
        surface = self._surface(toplevel=True)
        outline = gtk_ui.WindowOutline(surface)
        try:
            outline.show_around(Rect(60, 80, 600, 400))
            surface.place_overlay.assert_called_once_with(
                outline, Rect(60, 80, 600, 400)
            )
        finally:
            outline.destroy()

    def test_window_type_follows_the_surface_service(self):
        popup = gtk_ui.WindowOutline(self._surface(toplevel=False))
        toplevel = gtk_ui.WindowOutline(self._surface(toplevel=True))
        try:
            assert popup.get_window_type() == gtk_ui.Gtk.WindowType.POPUP
            assert toplevel.get_window_type() == gtk_ui.Gtk.WindowType.TOPLEVEL
        finally:
            popup.destroy()
            toplevel.destroy()

    def test_degenerate_rect_does_not_place(self):
        surface = self._surface(toplevel=False)
        outline = gtk_ui.WindowOutline(surface)
        try:
            outline.show_around(Rect(0, 0, 10, 0))
            surface.place_overlay.assert_not_called()
        finally:
            outline.destroy()
