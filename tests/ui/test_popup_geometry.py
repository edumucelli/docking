"""Anchor-monitor bounds and coordinate contracts, without a live compositor."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.backends.base import Rect, Size, SurfaceService
from docking.ui.display import ScreenPosition, clamp_popup, popup_workarea
from docking.ui.preview import preview_size


@pytest.mark.parametrize(
    "content,available,expected",
    [
        (Size(200, 150), Size(1000, 800), Size(200, 150)),
        (Size(1600, 150), Size(1000, 800), Size(1000, 163)),
        (Size(200, 1400), Size(1000, 800), Size(213, 800)),
        # One scrollbar makes the second necessary.
        (Size(1000, 801), Size(1000, 800), Size(1000, 800)),
        (Size(1001, 800), Size(1000, 800), Size(1000, 800)),
    ],
)
def test_size_includes_only_necessary_scrollbars(content, available, expected):
    assert preview_size(content, available, Size(13, 13)) == expected


def _popup(
    *,
    relative=False,
    external=None,
    geometry=None,
    workarea=None,
):
    monitor = MagicMock()
    monitor.get_geometry.return_value = geometry or Rect(-1280, 0, 1280, 800)
    monitor.get_workarea.return_value = workarea or Rect(-1280, 28, 1280, 719)
    monitor.get_scale_factor.return_value = 2
    display = MagicMock()
    primary = MagicMock()
    display.get_monitor_at_point.return_value = monitor
    display.get_primary_monitor.return_value = primary
    display.get_n_monitors.return_value = 2
    display.get_monitor.side_effect = [primary, monitor]
    surface = MagicMock(spec=SurfaceService)
    surface.external_workarea.return_value = external
    surface.popups_use_parent_relative_coordinates = relative
    surface.get_surface_position.return_value = (-1280, 637)
    popup = MagicMock()
    popup.get_display.return_value = display
    popup.get_transient_for.return_value = SimpleNamespace(surface_service=surface)
    return popup, surface


def test_anchor_monitor_uses_external_area_in_logical_coordinates():
    popup, surface = _popup(external=Rect(-1280, 28, 1280, 772))
    assert popup_workarea(popup, -1240, 760) == Rect(-1280, 28, 1280, 772)
    popup.get_display().get_monitor_at_point.assert_called_once_with(-1240, 760)
    snapshot = surface.external_workarea.call_args.args[0]
    assert snapshot.index == 1
    assert snapshot.scale == 2
    assert snapshot.primary is False


@pytest.mark.parametrize("relative", [False, True])
def test_missing_external_reservations_fall_back_to_gdk_workarea(relative):
    popup, _ = _popup(relative=relative)
    assert popup_workarea(popup, -1240, 760) == Rect(-1280, 28, 1280, 719)


@pytest.mark.parametrize("area", [Rect(0, 0, 10, 10), Rect(-1280, 0, 0, 0)])
def test_invalid_or_disjoint_workarea_falls_back_to_monitor(area):
    popup, _ = _popup(external=area)
    assert popup_workarea(popup, -1240, 760) == Rect(-1280, 0, 1280, 800)


def test_no_panel_uses_whole_monitor():
    popup, _ = _popup(workarea=Rect(-1280, 0, 1280, 800))
    assert popup_workarea(popup, -1240, 760) == Rect(-1280, 0, 1280, 800)


def test_workarea_is_intersected_with_anchor_monitor():
    popup, _ = _popup(external=Rect(-1400, 28, 2600, 1000))
    assert popup_workarea(popup, -1240, 760) == Rect(-1280, 28, 1280, 772)


@pytest.mark.parametrize("relative", [False, True])
def test_bounded_clamp_preserves_wayland_parent_relative_path(relative):
    popup, _ = _popup(relative=relative)
    result = clamp_popup(popup, -1400, 600, 400, 200, bounds=Rect(-1280, 28, 1280, 772))
    assert result == (
        ScreenPosition(-120, -37) if relative else ScreenPosition(-1280, 600)
    )
