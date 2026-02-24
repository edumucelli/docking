"""Tests for strut computation across all dock positions."""

import sys
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.core.position import Position  # noqa: E402
from docking.platform.struts import compute_struts  # noqa: E402

# Single 1920x1080 monitor at origin, scale=1
MX, MY, MW, MH = 0, 0, 1920, 1080
SW, SH = 1920, 1080
SCALE = 1
DOCK_H = 53  # icon_size(48) + bottom_padding(~5)


class TestBottomStruts:
    def test_sets_bottom_edge(self):
        s = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, SCALE, Position.BOTTOM)
        assert s[3] == DOCK_H  # bottom
        assert s[0] == s[1] == s[2] == 0  # other edges zero

    def test_bottom_span_covers_monitor_width(self):
        s = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, SCALE, Position.BOTTOM)
        assert s[10] == 0  # bottom_start_x
        assert s[11] == 1919  # bottom_end_x

    def test_unused_start_end_are_zero(self):
        s = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, SCALE, Position.BOTTOM)
        # left, right, top start/end pairs all zero
        assert s[4:10] == [0, 0, 0, 0, 0, 0]


class TestTopStruts:
    def test_sets_top_edge(self):
        s = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, SCALE, Position.TOP)
        assert s[2] == DOCK_H  # top
        assert s[0] == s[1] == s[3] == 0

    def test_top_span_covers_monitor_width(self):
        s = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, SCALE, Position.TOP)
        assert s[8] == 0  # top_start_x
        assert s[9] == 1919  # top_end_x


class TestLeftStruts:
    def test_sets_left_edge(self):
        s = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, SCALE, Position.LEFT)
        assert s[0] == DOCK_H  # left
        assert s[1] == s[2] == s[3] == 0

    def test_left_span_covers_monitor_height(self):
        s = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, SCALE, Position.LEFT)
        assert s[4] == 0  # left_start_y
        assert s[5] == 1079  # left_end_y


class TestRightStruts:
    def test_sets_right_edge(self):
        s = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, SCALE, Position.RIGHT)
        assert s[1] == DOCK_H  # right
        assert s[0] == s[2] == s[3] == 0

    def test_right_span_covers_monitor_height(self):
        s = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, SCALE, Position.RIGHT)
        assert s[6] == 0  # right_start_y
        assert s[7] == 1079  # right_end_y


class TestMultiMonitorGap:
    """When monitor doesn't extend to the logical screen edge, gap is added."""

    def test_bottom_gap_on_upper_monitor(self):
        # Given — monitor at top of a vertically stacked dual-monitor setup
        # Monitor: 1920x1080 at (0, 0), screen: 1920x2160
        s = compute_struts(DOCK_H, 0, 0, 1920, 1080, 1920, 2160, 1, Position.BOTTOM)
        # Then — strut includes the 1080px gap below the monitor
        assert s[3] == DOCK_H + 1080

    def test_right_gap_on_left_monitor(self):
        # Given — left monitor in a horizontally stacked setup
        # Monitor: 1920x1080 at (0, 0), screen: 3840x1080
        s = compute_struts(DOCK_H, 0, 0, 1920, 1080, 3840, 1080, 1, Position.RIGHT)
        assert s[1] == DOCK_H + 1920

    def test_top_gap_on_lower_monitor(self):
        # Given — monitor at y=1080 in vertical stack, screen: 1920x2160
        s = compute_struts(DOCK_H, 0, 1080, 1920, 1080, 1920, 2160, 1, Position.TOP)
        assert s[2] == DOCK_H + 1080

    def test_left_gap_on_right_monitor(self):
        # Given — monitor at x=1920 in horizontal stack, screen: 3840x1080
        s = compute_struts(DOCK_H, 1920, 0, 1920, 1080, 3840, 1080, 1, Position.LEFT)
        assert s[0] == DOCK_H + 1920


class TestHiDPIScale:
    """Struts are in physical pixels — all values multiplied by scale."""

    def test_scale_2x_doubles_all_values(self):
        s1 = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, 1, Position.BOTTOM)
        s2 = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, 2, Position.BOTTOM)
        assert s2[3] == s1[3] * 2
        assert s2[10] == s1[10] * 2
        # end_x: (1920*2 - 1) = 3839 vs (1920*1 - 1) = 1919
        assert s2[11] == 1920 * 2 - 1


class TestAlwaysTwelveValues:
    def test_all_positions_return_12_values(self):
        for pos in Position:
            s = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, SCALE, pos)
            assert len(s) == 12

    def test_only_one_edge_nonzero(self):
        for pos in Position:
            s = compute_struts(DOCK_H, MX, MY, MW, MH, SW, SH, SCALE, pos)
            edge_values = s[0:4]
            assert sum(1 for v in edge_values if v > 0) == 1
