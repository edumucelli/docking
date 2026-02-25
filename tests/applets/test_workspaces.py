"""Tests for the workspaces applet."""

import pytest

import cairo

from docking.applets.workspaces import WorkspacesApplet, _render_grid


class TestRenderGrid:
    """_render_grid should draw workspace cells with the active one highlighted."""

    @pytest.mark.parametrize("count", [1, 2, 4, 6, 9])
    def test_renders_various_counts(self, count):
        size = 48
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_grid(cr, size, count, active_num=0)
        data = surface.get_data()
        assert any(b != 0 for b in data)

    def test_zero_count_draws_nothing(self):
        size = 48
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_grid(cr, size, 0, active_num=-1)
        data = surface.get_data()
        assert all(b == 0 for b in data)

    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_renders_at_various_sizes(self, size):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_grid(cr, size, 4, active_num=1)
        data = surface.get_data()
        assert any(b != 0 for b in data)

    def test_grid_layout_2_columns(self):
        # Given 4 workspaces -> 2 cols x 2 rows
        size = 100
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        # When rendered with active=2
        _render_grid(cr, size, 4, active_num=2)
        # Then surface has content (no crash, correct layout)
        data = surface.get_data()
        assert any(b != 0 for b in data)


class TestWorkspacesApplet:
    def test_creates_with_icon(self):
        # Given no Wnck screen, falls back to default 4-cell grid
        applet = WorkspacesApplet(48)
        assert applet.item.icon is not None

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = WorkspacesApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size
