"""Microbenchmarks for Docking's hover/draw hot paths.

This script does not try to simulate a full GTK main loop. Its purpose is
narrower: establish stable before/after numbers for the pure and mostly-pure
pieces that dominate Docking's per-frame work on Wayland/XWayland.

The benchmarks are intended for optimization comparisons, not absolute
performance claims across machines. Re-run this script before and after a
change; compare the relative movement in the reported medians/p95 values.

Current benchmark coverage:

- geometry frame construction
- hover update with and without a caller-supplied geometry frame
- blur region computation
- renderer draw cost with and without icon surfaces
- a CPU-only approximation of the current motion path
- a CPU-only approximation of the current draw path

These numbers intentionally exclude the GTK compositor/presentation layer. They
tell us what Docking itself is doing before GTK/XWayland/Mutter get involved.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

import docking.ui.renderer as renderer_mod
from docking.core.position import Position, is_horizontal
from docking.core.theme import Theme
from docking.platform.backends.x11.impl.struts import BlurRect, compute_blur_region
from docking.platform.model import DockItem
from docking.ui.autohide import HideState
from docking.ui.geometry import DockGeometryBuilder, Rect, build_geometry_frame
from docking.ui.hover import HoverManager
from docking.ui.renderer import DockRenderer

WINDOW_W = 1920
WINDOW_H = 102
ICON_SIZE = 48
ITEM_COUNT = 20
SAMPLES = 400
WARMUP = 40


class FakePixbuf:
    def __init__(self, width: int = 64, height: int = 64) -> None:
        self._width = width
        self._height = height
        pixel = bytes((180, 120, 240, 255))
        self._pixels = pixel * width * height

    def get_width(self) -> int:
        return self._width

    def get_height(self) -> int:
        return self._height

    def get_pixels(self) -> bytes:
        return self._pixels

    def get_n_channels(self) -> int:
        return 4

    def get_rowstride(self) -> int:
        return self._width * 4


class FakeTooltip:
    def update(self, _item: object, _frame: object) -> None:
        return

    def hide(self) -> None:
        return


class FakeDrawingArea:
    def __init__(self, width: int, height: int) -> None:
        self._allocation = SimpleNamespace(width=width, height=height)

    def get_allocation(self) -> object:
        return self._allocation

    def queue_draw(self) -> None:
        return


class FakeWindow:
    def __init__(
        self,
        *,
        items: list[DockItem],
        config: object,
        theme: Theme,
        autohide_state: HideState = HideState.VISIBLE,
        hide_offset: float = 0.0,
    ) -> None:
        self.model = SimpleNamespace(visible_items=lambda: items)
        self.config = config
        self.theme = theme
        self.cursor_x = -1.0
        self.cursor_y = -1.0
        self.dock_hovered = True
        self.drawing_area = FakeDrawingArea(WINDOW_W, WINDOW_H)
        self.zoom_animator = SimpleNamespace(progress=1.0)
        self.autohide = SimpleNamespace(
            enabled=True,
            state=autohide_state,
            zoom_progress=1.0,
            hide_offset=hide_offset,
        )

    def get_size(self) -> tuple[int, int]:
        return WINDOW_W, WINDOW_H

    def get_realized(self) -> bool:
        return True

    def get_position(self) -> tuple[int, int]:
        return 0, 0


@dataclass
class BenchResult:
    name: str
    samples: int
    median_us: float
    p95_us: float
    mean_us: float
    min_us: float
    max_us: float


def _fake_cairo_set_source_pixbuf(
    cr: cairo.Context,
    pixbuf: FakePixbuf,
    _x: float,
    _y: float,
) -> None:
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    cr.rectangle(0, 0, width, height)
    cr.set_source_rgba(0.9, 0.9, 0.95, 1.0)
    cr.fill()


def _config() -> object:
    return SimpleNamespace(
        pos=Position.BOTTOM,
        icon_size=ICON_SIZE,
        zoom_percent=1.5,
        zoom_enabled=True,
        previews_enabled=False,
        tooltips_enabled=True,
    )


def _items(*, with_icons: bool) -> list[DockItem]:
    items: list[DockItem] = []
    for index in range(ITEM_COUNT):
        items.append(
            DockItem(
                desktop_id=f"app{index}.desktop",
                name=f"App {index}",
                is_running=index < 8,
                is_active=index == 3,
                instance_count=2 if index < 4 else 1,
                icon=FakePixbuf() if with_icons else None,
            )
        )
    return items


def _clone_items(items: list[DockItem]) -> list[DockItem]:
    clones: list[DockItem] = []
    for item in items:
        clones.append(
            DockItem(
                desktop_id=item.desktop_id,
                kind=item.kind,
                target=item.target,
                name=item.name,
                icon_name=item.icon_name,
                wm_class=item.wm_class,
                is_pinned=item.is_pinned,
                is_running=item.is_running,
                is_active=item.is_active,
                is_urgent=item.is_urgent,
                instance_count=item.instance_count,
                icon=item.icon,
                main_size=item.main_size,
                last_clicked=item.last_clicked,
                last_launched=item.last_launched,
                last_urgent=item.last_urgent,
                tooltip_builder=item.tooltip_builder,
                prefs_key=item.prefs_key,
                allow_zoom=item.allow_zoom,
                insert_factor=item.insert_factor,
                removal_index=item.removal_index,
            )
        )
    return clones


def _surface_context(width: int = WINDOW_W, height: int = WINDOW_H) -> cairo.Context:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    return cairo.Context(surface)


def _baseline_frame(items: list[DockItem], config: object, theme: Theme) -> object:
    return build_geometry_frame(
        items=items,
        config=config,
        theme=theme,
        window_w=WINDOW_W,
        window_h=WINDOW_H,
        cursor_main=WINDOW_W / 2,
        autohide_state=HideState.VISIBLE,
        zoom_progress=1.0,
        hide_offset=0.0,
    )


def _cursor_points(frame: object) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for geometry in frame.item_geometries:
        rect = geometry.hover_rect
        points.append((rect.x + rect.w / 2, rect.y + rect.h / 2))
    return points


def _bench(name: str, func: Any, *, samples: int = SAMPLES) -> BenchResult:
    for _ in range(WARMUP):
        func()

    timings_us: list[float] = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        func()
        elapsed_us = (time.perf_counter_ns() - start) / 1_000.0
        timings_us.append(elapsed_us)

    return BenchResult(
        name=name,
        samples=samples,
        median_us=statistics.median(timings_us),
        p95_us=statistics.quantiles(timings_us, n=100)[94],
        mean_us=statistics.fmean(timings_us),
        min_us=min(timings_us),
        max_us=max(timings_us),
    )


def _print_table(results: list[BenchResult]) -> None:
    header = (
        f"{'benchmark':34} {'median_us':>10} {'p95_us':>10} "
        f"{'mean_us':>10} {'min_us':>10} {'max_us':>10}"
    )
    print(header)
    print("-" * len(header))
    for result in results:
        print(
            f"{result.name:34} "
            f"{result.median_us:10.1f} "
            f"{result.p95_us:10.1f} "
            f"{result.mean_us:10.1f} "
            f"{result.min_us:10.1f} "
            f"{result.max_us:10.1f}"
        )


def run_benchmarks() -> list[BenchResult]:
    renderer_mod.Gdk.cairo_set_source_pixbuf = _fake_cairo_set_source_pixbuf

    config = _config()
    theme = Theme.load("default", ICON_SIZE)
    items_with_icons = _items(with_icons=True)
    items_without_icons = _items(with_icons=False)
    items_with_icons_hover = _clone_items(items_with_icons)
    items_with_icons_click = _clone_items(items_with_icons)
    items_with_icons_hover_click = _clone_items(items_with_icons)
    items_with_icons_click[3].last_clicked = 950_000
    items_with_icons_hover_click[3].last_clicked = 950_000

    frame_with_icons = _baseline_frame(items_with_icons, config, theme)
    frame_with_icons_idle = _baseline_frame(
        _clone_items(items_with_icons), config, theme
    )
    frame_with_icons_hover = _baseline_frame(items_with_icons_hover, config, theme)
    frame_with_icons_click = _baseline_frame(items_with_icons_click, config, theme)
    frame_with_icons_hover_click = _baseline_frame(
        items_with_icons_hover_click, config, theme
    )
    frame_without_icons = _baseline_frame(items_without_icons, config, theme)

    cursor_points = _cursor_points(frame_with_icons)
    cursor_index = 0

    def next_cursor() -> tuple[float, float]:
        nonlocal cursor_index
        point = cursor_points[cursor_index % len(cursor_points)]
        cursor_index += 1
        return point

    geometry_cursor = 0

    def geometry_build() -> object:
        nonlocal geometry_cursor
        cursor = 120.0 + (geometry_cursor % 600)
        geometry_cursor += 19
        return build_geometry_frame(
            items=items_with_icons,
            config=config,
            theme=theme,
            window_w=WINDOW_W,
            window_h=WINDOW_H,
            cursor_main=cursor,
            autohide_state=HideState.VISIBLE,
            zoom_progress=1.0,
            hide_offset=0.0,
        )

    window = FakeWindow(items=items_with_icons, config=config, theme=theme)
    geometry_builder = DockGeometryBuilder(window=window)
    hover = HoverManager(
        window=window,
        config=config,
        model=window.model,
        theme=theme,
        tooltip=FakeTooltip(),
        geometry_builder=geometry_builder,
    )

    def hover_update_with_frame() -> None:
        point = next_cursor()
        window.cursor_x, window.cursor_y = point
        cursor_main = (
            window.cursor_x if is_horizontal(pos=config.pos) else window.cursor_y
        )
        frame = geometry_build()
        hover.update(cursor_main, frame=frame)

    hover_no_frame = HoverManager(
        window=window,
        config=config,
        model=window.model,
        theme=theme,
        tooltip=FakeTooltip(),
        geometry_builder=geometry_builder,
    )

    def hover_update_without_frame() -> None:
        point = next_cursor()
        window.cursor_x, window.cursor_y = point
        cursor_main = (
            window.cursor_x if is_horizontal(pos=config.pos) else window.cursor_y
        )
        hover_no_frame.update(cursor_main, frame=None)

    renderer_with_icons = DockRenderer()
    widget = FakeDrawingArea(WINDOW_W, WINDOW_H)
    renderer_mod.GLib.get_monotonic_time = lambda: 1_000_000

    def renderer_draw_with_icons_idle() -> None:
        renderer_with_icons.draw(
            cr=_surface_context(),
            widget=widget,
            frame=frame_with_icons_idle,
            config=config,
            theme=theme,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            hovered_id="",
        )

    def renderer_draw_with_icons_hover() -> None:
        renderer_with_icons.draw(
            cr=_surface_context(),
            widget=widget,
            frame=frame_with_icons_hover,
            config=config,
            theme=theme,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            hovered_id="app3.desktop",
        )

    def renderer_draw_with_icons_click() -> None:
        renderer_with_icons.draw(
            cr=_surface_context(),
            widget=widget,
            frame=frame_with_icons_click,
            config=config,
            theme=theme,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            hovered_id="",
        )

    def renderer_draw_with_icons_hover_click() -> None:
        renderer_with_icons.draw(
            cr=_surface_context(),
            widget=widget,
            frame=frame_with_icons_hover_click,
            config=config,
            theme=theme,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            hovered_id="app3.desktop",
        )

    renderer_without_icons = DockRenderer()

    def renderer_draw_without_icons() -> None:
        renderer_without_icons.draw(
            cr=_surface_context(),
            widget=widget,
            frame=frame_without_icons,
            config=config,
            theme=theme,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            hovered_id="app3.desktop",
        )

    blur_rect = BlurRect(
        x=frame_with_icons.background_rect.x,
        y=frame_with_icons.background_rect.y,
        width=frame_with_icons.background_rect.w,
        height=frame_with_icons.background_rect.h,
    )

    def blur_region_compute() -> list[int]:
        return compute_blur_region(
            rect=blur_rect,
            roundness=theme.roundness,
            round_bottom=theme.round_bottom,
            position=config.pos,
            scale=1,
        )

    applied_input_rect: Rect | None = None

    def motion_pass_cpu_only() -> None:
        nonlocal applied_input_rect
        point = next_cursor()
        window.cursor_x, window.cursor_y = point
        frame = geometry_build()
        if frame.cursor_rect != applied_input_rect:
            applied_input_rect = frame.cursor_rect
        cursor_main = (
            window.cursor_x if is_horizontal(pos=config.pos) else window.cursor_y
        )
        hover.update(cursor_main, frame=frame)

    applied_input_rect_draw: Rect | None = None
    renderer_draw_pass = DockRenderer()

    def draw_pass_cpu_only() -> None:
        nonlocal applied_input_rect_draw
        frame = geometry_build()
        compute_blur_region(
            rect=BlurRect(
                x=frame.background_rect.x,
                y=frame.background_rect.y,
                width=frame.background_rect.w,
                height=frame.background_rect.h,
            ),
            roundness=theme.roundness,
            round_bottom=theme.round_bottom,
            position=config.pos,
            scale=1,
        )
        renderer_draw_pass.draw(
            cr=_surface_context(),
            widget=widget,
            frame=frame,
            config=config,
            theme=theme,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            hovered_id="app3.desktop",
        )
        if frame.cursor_rect != applied_input_rect_draw:
            applied_input_rect_draw = frame.cursor_rect

    return [
        _bench("geometry.build_frame", geometry_build),
        _bench("hover.update(frame=...)", hover_update_with_frame),
        _bench("hover.update(no frame)", hover_update_without_frame),
        _bench("struts.compute_blur_region", blur_region_compute),
        _bench("renderer.draw(no icons)", renderer_draw_without_icons),
        _bench("renderer.draw(icons idle)", renderer_draw_with_icons_idle),
        _bench("renderer.draw(icons hover)", renderer_draw_with_icons_hover),
        _bench("renderer.draw(icons click)", renderer_draw_with_icons_click),
        _bench(
            "renderer.draw(icons hover+click)",
            renderer_draw_with_icons_hover_click,
        ),
        _bench("motion pass cpu-only", motion_pass_cpu_only),
        _bench("draw pass cpu-only", draw_pass_cpu_only),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Emit JSON results")
    args = parser.parse_args()

    results = run_benchmarks()
    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
        return
    _print_table(results)


if __name__ == "__main__":
    main()
