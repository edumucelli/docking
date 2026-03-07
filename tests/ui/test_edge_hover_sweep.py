"""Sweep-style regressions for outer-edge hover and leave behavior.

These tests do not mock the geometry math. They build a real dock geometry
frame, then move a synthetic cursor across the dock one pixel at a time.
That gives us a repeatable loop for investigating "snap on outer edge" bugs
without relying on manual desktop interaction.
"""

from __future__ import annotations

from types import SimpleNamespace

from docking.core.position import Position
from docking.platform.model import DockItem
from docking.ui.geometry import DockGeometryFrame, build_geometry_frame


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        pos=Position.BOTTOM,
        icon_size=48,
        zoom_percent=1.5,
        zoom_enabled=True,
    )


def _theme() -> SimpleNamespace:
    return SimpleNamespace(
        distance_from_edge=0,
        item_padding=8,
        h_padding=12,
        top_padding=0,
        bottom_padding=4,
        shelf_height=21,
        stroke_width=1.0,
    )


def _items() -> list[DockItem]:
    return [
        DockItem(desktop_id="google-chrome.desktop", name="Google Chrome"),
        DockItem(desktop_id="firefox.desktop", name="Firefox"),
        DockItem(desktop_id="code.desktop", name="Code"),
        DockItem(desktop_id="terminator.desktop", name="Terminator"),
        DockItem(desktop_id="sublime_text.desktop", name="Sublime Text"),
        DockItem(desktop_id="applet://clock", name="Clock"),
        DockItem(desktop_id="applet://calendar", name="Calendar"),
    ]


def _frame(cursor_main: float) -> DockGeometryFrame:
    return build_geometry_frame(
        items=_items(),
        config=_config(),
        theme=_theme(),
        window_w=1920,
        window_h=122,
        cursor_main=cursor_main,
        autohide_state=None,
        zoom_progress=1.0,
        hide_offset=0.0,
    )


def _sweep_until_none(
    *,
    frame: DockGeometryFrame,
    start_x: int,
    stop_x: int,
    step: int,
    y: int,
) -> int:
    for x in range(start_x, stop_x, step):
        if frame.item_at_point(x, y) is None:
            return x
    raise AssertionError("sweep never exited hover region")


class TestEdgeHoverSweep:
    def test_first_item_loses_hover_exactly_after_left_background_edge(self):
        frame = _frame(cursor_main=330.0)
        first = frame.item_geometries[0]
        y = first.draw_rect.y + first.draw_rect.h - 1

        exit_x = _sweep_until_none(
            frame=frame,
            start_x=int(first.anchor_x),
            stop_x=frame.background_rect.x - 8,
            step=-1,
            y=y,
        )

        assert exit_x == frame.background_rect.x - 1

    def test_last_item_loses_hover_exactly_at_right_background_edge(self):
        frame = _frame(cursor_main=1590.0)
        last = frame.item_geometries[-1]
        y = last.draw_rect.y + last.draw_rect.h - 1

        exit_x = _sweep_until_none(
            frame=frame,
            start_x=int(last.anchor_x),
            stop_x=frame.background_rect.x + frame.background_rect.w + 8,
            step=1,
            y=y,
        )

        assert exit_x == frame.background_rect.x + frame.background_rect.w

    def test_cursor_band_has_no_main_axis_gaps_between_background_edges(self):
        frame = _frame(cursor_main=960.0)
        y = frame.static_dock_rect.y + frame.static_dock_rect.h - 1
        first_gap = None

        for x in range(
            frame.background_rect.x,
            frame.background_rect.x + frame.background_rect.w,
        ):
            if frame.item_at_point(x, y) is None:
                first_gap = x
                break

        assert first_gap is None
