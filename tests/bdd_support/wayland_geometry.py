"""Production surface policy and geometry with explicit compositor/GTK fixtures.

Native configure delivery and input are tested separately under Sway and KWin.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.core.config import Config
from docking.core.items import DockItem
from docking.core.position import Position, is_horizontal
from docking.core.theme import Theme
from docking.platform.backends.base import MonitorSnapshot, PlacementRequest, Rect, Size
from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
from docking.ui.geometry import DockGeometryBuilder, compute_dock_cross_metrics


class ConfiguredLayerDock:
    def __init__(self, position: str, count: int = 13):
        self.config = Config(position=position, icon_size=48, zoom_enabled=False)
        self.theme = Theme.load("default", 48)
        self.items = [DockItem(desktop_id=f"item-{i}") for i in range(count)]
        self.layer_shell = SimpleNamespace(
            Edge=SimpleNamespace(TOP=0, BOTTOM=1, LEFT=2, RIGHT=3),
            Layer=SimpleNamespace(TOP=2),
            KeyboardMode=SimpleNamespace(NONE=0),
            init_for_window=MagicMock(),
            set_anchor=MagicMock(),
            set_exclusive_zone=MagicMock(),
        )
        self.surface = WaylandLayerShellSurfaceService(layer_shell=self.layer_shell)
        self.window = MagicMock(
            config=self.config,
            theme=self.theme,
            surface_service=self.surface,
            model=SimpleNamespace(visible_items=lambda: self.items),
            cursor_x=-1.0,
            cursor_y=-1.0,
            autohide=SimpleNamespace(enabled=False),
            zoom_animator=SimpleNamespace(progress=1.0),
        )
        self.surface.configure_before_realize(self.window)
        self.builder = DockGeometryBuilder(self.window)
        self.place(position)

    def place(self, position: str):
        self.config.position = position
        horizontal = is_horizontal(self.config.pos)
        self.cross = compute_dock_cross_metrics(
            icon_size=48, zoom=1.5, theme=self.theme
        ).surface_extent
        self.full_main = 1280 if horizontal else 800
        self.surface.position_or_anchor(
            PlacementRequest(
                monitor=MonitorSnapshot(
                    index=0,
                    geometry=Rect(0, 0, 1280, 800),
                    workarea=Rect(0, 0, 1280, 800),
                    scale=1,
                    primary=True,
                ),
                position=Position(position),
                x=0,
                y=0,
                size=Size(self.full_main, self.cross)
                if horizontal
                else Size(self.cross, self.full_main),
            )
        )

    def configure(self, reserved: int):
        self.available = self.full_main - reserved
        horizontal = is_horizontal(self.config.pos)
        suggested = (
            (self.available, self.cross) if horizontal else (self.cross, self.available)
        )
        minimum = self.window.set_size_request.call_args.args
        self.window.get_size.return_value = tuple(
            max(value, lower) for value, lower in zip(suggested, minimum, strict=True)
        )
        self.frame = self.builder.build_frame()
        return self.frame

    def assert_fits(self):
        width, height = self.window.get_size()
        assert (width if is_horizontal(self.config.pos) else height) == self.available
        assert (height if is_horizontal(self.config.pos) else width) == self.cross
        for geometry in self.frame.item_geometries:
            r = geometry.draw_rect
            if not r.w or not r.h:
                continue
            assert r.x >= 0 and r.x + r.w <= width
            assert r.y >= 0 and r.y + r.h <= height
            assert (
                self.frame.item_at_point(r.x + r.w / 2, r.y + r.h / 2) is geometry.item
            )
        assert self.config.icon_size == 48
