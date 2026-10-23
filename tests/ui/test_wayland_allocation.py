"""F13: compositor-sized content shares paint and input bounds."""

import pytest

from docking.core.items import DockItem
from docking.core.position import Position
from docking.platform.backends.reduced.services import ReducedSurfaceService
from docking.ui.geometry import build_geometry_frame
from tests.bdd_support.wayland_geometry import ConfiguredLayerDock


@pytest.mark.parametrize("position", list(Position))
def test_panel_lifecycle_and_repeated_placement_fit_actual_allocation(position):
    dock = ConfiguredLayerDock(position)
    for reservation in (0, 64, 112, 240, 0):
        dock.configure(reservation)
        dock.assert_fits()
        dock.place(position)
        dock.configure(reservation)
        dock.assert_fits()
    assert all(g.draw_rect.w == 48 for g in dock.frame.item_geometries)
    dock.window.set_resizable.assert_called_once_with(True)


def test_changing_axes_releases_the_previous_minimum():
    dock = ConfiguredLayerDock("bottom")
    for edge in ("bottom", "right", "top", "left", "bottom"):
        dock.place(edge)
        dock.configure(112)
        dock.assert_fits()


@pytest.mark.parametrize("position", list(Position))
@pytest.mark.parametrize("cursor", [-1, 0, 20, 344, 667, 687])
def test_zoom_and_insertion_keep_icons_inside_assigned_bounds(position, cursor):
    dock = ConfiguredLayerDock(position)
    dock.config.zoom_enabled = True
    dock.config.zoom_percent = 2.0
    dock.configure(dock.full_main - 688)
    for insertion in (-1, 0, 6, 13):
        dock.frame = dock.builder.build_frame(
            main_cursor=cursor, drop_insert_index=insertion
        )
        dock.assert_fits()
        background = dock.frame.background_rect
        if position in (Position.TOP, Position.BOTTOM):
            assert background.x >= 0 and background.x + background.w <= 688
        else:
            assert background.y >= 0 and background.y + background.h <= 688


@pytest.mark.parametrize("position", list(Position))
def test_custom_and_collapsed_item_widths_remain_valid(position):
    dock = ConfiguredLayerDock(position)
    dock.items[3] = DockItem(desktop_id="wide", main_size=96)
    dock.items[4].insert_factor = 0.0
    dock.items[5].insert_factor = 0.5
    dock.configure(dock.full_main - 688)
    dock.assert_fits()
    hidden = dock.frame.item_geometries[4]
    assert hidden.draw_rect.w == 0 and hidden.hit_rect.w == 0
    assert (
        dock.frame.item_geometries[3].draw_rect.w
        > dock.frame.item_geometries[0].draw_rect.w
    )


def test_non_layer_shell_geometry_is_not_rescaled():
    dock = ConfiguredLayerDock("left")
    dock.configure(112)
    dock.window.surface_service = ReducedSurfaceService()
    frame = dock.builder.build_frame()
    assert all(g.draw_rect.w == 48 for g in frame.item_geometries)


@pytest.mark.parametrize("position", list(Position))
def test_fitting_does_not_change_uncrowded_layout(position):
    dock = ConfiguredLayerDock(position, count=3)
    frame = dock.configure(112)
    width, height = dock.window.get_size()
    control = build_geometry_frame(
        items=dock.items,
        config=dock.config,
        theme=dock.theme,
        window_w=width,
        window_h=height,
        cursor_main=-1,
        autohide_state=None,
    )
    assert frame == control
