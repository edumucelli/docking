"""Tests for drag-and-drop URI parsing and internal reorder placement."""

import sys
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.ui.dnd as dnd_mod
from docking.core.items import APP_KIND
from docking.core.position import Position
from docking.platform.applications.entries import desktop_id_from_uri_or_path
from docking.platform.model import DockItem, DockModel
from docking.ui.autohide import HideState
from docking.ui.dnd import DRAG_ICON_SCALE, DnDHandler
from docking.ui.geometry import DockGeometryBuilder


class TestConstants:
    def test_drag_icon_scale_reasonable(self):
        # Given / When / Then
        assert DRAG_ICON_SCALE > 1.0
        assert DRAG_ICON_SCALE < 2.0


class TestPoofAsset:
    def test_poof_svg_exists(self):
        # Given
        from pathlib import Path

        svg = Path(__file__).parent.parent.parent / "docking" / "assets" / "poof.svg"
        # When / Then
        assert svg.exists(), "poof.svg sprite sheet missing"

    def test_poof_svg_path_matches_poof_module(self):
        """Asset path resolved in tests matches the path used in poof.py."""
        # Given
        from pathlib import Path

        import docking.ui.poof as poof_mod

        poof_py = Path(poof_mod.__file__)
        # When
        expected = (poof_py.parent.parent / "assets" / "poof.svg").resolve()
        test_path = (
            Path(__file__).parent.parent.parent / "docking" / "assets" / "poof.svg"
        ).resolve()
        # Then
        assert expected == test_path

    def test_poof_svg_dimensions(self):
        """SVG should be square-width with multiple frames stacked vertically."""
        # Given
        from pathlib import Path

        svg = Path(__file__).parent.parent.parent / "docking" / "assets" / "poof.svg"
        # When
        with svg.open() as f:
            content = f.read(500)
        import re

        match = re.search(r'viewBox="0 0 (\d+) (\d+)"', content)
        # Then
        assert match, "poof.svg missing viewBox"
        w, h = int(match.group(1)), int(match.group(2))
        assert w > 0
        assert h > w, "sprite sheet should be taller than wide (stacked frames)"
        assert h % w == 0, "height should be exact multiple of width"
        assert h // w >= 4, "should have at least 4 frames"


class TestAppletUriRejection:
    """Applet URIs (e.g. from Plank) must not be accepted as desktop files.

    Previously, dropping a Plank applet would open a gap that never closed
    because the URI failed to resolve but drop_insert_index was never cleared.
    """

    def test_applet_uri_returns_none(self):
        # Given
        uri = "applet://clock"
        # When / Then
        assert desktop_id_from_uri_or_path(uri) is None

    def test_applet_uri_systemmonitor(self):
        assert desktop_id_from_uri_or_path("applet://systemmonitor") is None

    def test_applet_uri_trash(self):
        assert desktop_id_from_uri_or_path("applet://trash") is None


class TestUriToDesktopId:
    def test_file_uri(self, tmp_path):
        # Given
        uri = (tmp_path / "firefox.desktop").as_uri()
        # When / Then
        assert desktop_id_from_uri_or_path(uri) == "firefox.desktop"

    def test_file_uri_with_spaces(self, tmp_path):
        # Given
        uri = (tmp_path / "my app.desktop").as_uri()
        # When / Then
        assert desktop_id_from_uri_or_path(uri) == "my app.desktop"

    @pytest.mark.skipif(sys.platform == "win32", reason="Snap uses POSIX file URIs")
    def test_file_uri_snap(self):
        # Given
        uri = "file:///var/lib/snapd/desktop/applications/firefox_firefox.desktop"
        # When / Then
        assert desktop_id_from_uri_or_path(uri) == "firefox_firefox.desktop"

    def test_plain_path(self):
        # Given
        uri = "firefox.desktop"
        # When / Then
        assert desktop_id_from_uri_or_path(uri) == "firefox.desktop"

    def test_non_desktop_file_returns_none(self):
        # Given
        uri = "file:///home/user/document.pdf"
        # When / Then
        assert desktop_id_from_uri_or_path(uri) is None

    def test_http_uri_returns_none(self):
        # Given
        uri = "https://example.com/app.desktop"
        # When / Then
        assert desktop_id_from_uri_or_path(uri) is None

    def test_empty_string_returns_none(self):
        # Given / When / Then
        assert desktop_id_from_uri_or_path("") is None

    def test_non_desktop_plain_path(self):
        # Given / When / Then
        assert desktop_id_from_uri_or_path("readme.txt") is None


_DRAG_IDS = ("a.desktop", "b.desktop", "c.desktop", "d.desktop")


def _order_with_dragged_at(desktop_id: str, index: int) -> list[str]:
    rest = [did for did in _DRAG_IDS if did != desktop_id]
    return [*rest[:index], desktop_id, *rest[index:]]


class _ReorderDock:
    """Dock stub backed by the real model, geometry, and DnD handler.

    Only services the drag path never reads are faked, so drag motion runs the
    real index math and the real reorder, persistence included.
    """

    def __init__(self) -> None:
        self.config = SimpleNamespace(
            pos=Position.BOTTOM,
            icon_size=48,
            zoom_percent=1.5,
            scaled_icon_size=48,
            zoom_enabled=True,
            lock_icons=False,
            show_recent_apps=False,
            anchor_applets=False,
            anchor_files=False,
            pinned=[],
            item_prefs={},
            save=MagicMock(),
            additional_distance_from_edge=0,
        )
        self.theme = SimpleNamespace(
            item_padding=8,
            horizontal_padding=12,
            top_padding=0,
            bottom_padding=4,
            shelf_height=21,
            stroke_width=1.0,
            distance_from_edge=0,
            urgent_glow_time_ms=500,
        )
        self.model = DockModel(
            config=self.config,
            applet_services=MagicMock(),
            application_registry=MagicMock(),
            application_launcher=MagicMock(),
            icon_loader=MagicMock(),
            target_service=MagicMock(),
            recent_applications=MagicMock(),
        )
        self.cursor_x = 0.0
        self.cursor_y = 0.0
        self.autohide = SimpleNamespace(
            enabled=True,
            state=HideState.VISIBLE,
            zoom_progress=1.0,
            hide_offset=0.0,
            set_disabled=MagicMock(),
            set_hovered=MagicMock(),
            on_mouse_enter=MagicMock(),
            on_mouse_leave=MagicMock(),
        )
        self.zoom_animator = SimpleNamespace(progress=1.0)
        self.surface_service = SimpleNamespace(compositor_sizes_main_axis=False)
        self.geometry = DockGeometryBuilder(cast(Any, self))
        self.drawing_area = MagicMock()
        self.dnd = DnDHandler(
            drawing_area=self.drawing_area,
            window=cast(Any, self),
            model=self.model,
            config=cast(Any, self.config),
            renderer=SimpleNamespace(slide_offsets={}, prev_positions={}),
            theme=cast(Any, self.theme),
            geometry_builder=self.geometry,
            folder_stack=MagicMock(),
            application_registry=MagicMock(),
            application_launcher=MagicMock(),
            icon_loader=MagicMock(),
            target_service=MagicMock(),
        )
        self.reset()

    def get_size(self) -> tuple[int, int]:
        return (1920, 122)

    def reset(self) -> None:
        """Restore the initial pinned order, as a fresh drag would find it."""
        self.model.pinned_items = [
            DockItem(desktop_id=did, name=did, kind=APP_KIND, is_pinned=True)
            for did in _DRAG_IDS
        ]

    def order(self) -> list[str]:
        return [item.desktop_id for item in self.model.visible_items()]

    def slot_centres(self) -> list[float]:
        frame = self.geometry.build_frame(main_cursor=-1.0)
        return [geo.anchor_x for geo in frame.item_geometries]

    def start_drag(self, index: int) -> None:
        """Leave the handler in the state drag-begin leaves for an internal drag."""
        self.dnd._drag_from = index
        self.dnd.drag_index = index
        self.dnd._dragged_item = self.model.visible_items()[index]

    def drag_to(self, cursor_x: float) -> None:
        self.dnd._on_drag_motion(self.drawing_area, MagicMock(), int(cursor_x), 60, 0)


class TestInternalReorderDragMotion:
    """Internal drags preserve the grabbed object while the visible order changes.

    Insertion gaps must account for the source before the model pops it.
    """

    def test_drag_tracks_the_grabbed_item_when_visible_indices_change(self):
        dock = _ReorderDock()
        dock.start_drag(1)
        dock.model.pinned_items.insert(0, DockItem("new.desktop", is_pinned=True))

        dock.drag_to(dock.slot_centres()[-1] + 100)

        assert dock.order() == [
            "new.desktop",
            "a.desktop",
            "c.desktop",
            "d.desktop",
            "b.desktop",
        ]
        assert dock.dnd.drag_index == 4

    def test_refused_reorder_keeps_the_actual_drag_index(self):
        dock = _ReorderDock()
        dock.start_drag(0)
        dock.model.reorder_visible = MagicMock()

        dock.drag_to(dock.slot_centres()[-1] + 100)

        assert dock.order() == list(_DRAG_IDS)
        assert dock.dnd.drag_index == 0

    def test_disappearing_drag_source_does_not_reorder_its_neighbor(self):
        dock = _ReorderDock()
        dock.start_drag(1)
        dock.model.pinned_items.pop(1)

        dock.drag_to(dock.slot_centres()[-1] + 100)

        assert dock.order() == ["a.desktop", "c.desktop", "d.desktop"]
        assert dock.dnd.drag_index == -1
        dock.config.save.assert_not_called()

    @pytest.fixture(autouse=True)
    def _stub_drag_status(self, monkeypatch):
        """Fake Gdk.drag_status only -- a real one needs a live drag context."""
        monkeypatch.setattr(dnd_mod.Gdk, "drag_status", lambda *_args: None)

    def test_rightward_drag_visits_every_slot_under_the_cursor(self):
        # Given the leftmost icon picked up and dragged rightwards 1px at a time
        dock = _ReorderDock()
        centres = dock.slot_centres()
        dock.start_drag(0)
        # When / Then
        for cursor_x in range(int(centres[0]), int(centres[-1]) + 60):
            dock.drag_to(cursor_x)
            index = dock.order().index("a.desktop")
            expected = max(
                (slot for slot, centre in enumerate(centres) if centre <= cursor_x),
                default=0,
            )
            assert index == expected, (
                f"cursor at {cursor_x} rested the icon in slot {index}, "
                f"expected slot {expected}"
            )

    def test_cursor_at_each_slot_centre_selects_that_slot(self):
        # Given a drag of the leftmost icon whose motion arrives as one event
        dock = _ReorderDock()
        centres = dock.slot_centres()
        # When / Then
        for slot, centre in enumerate(centres):
            dock.reset()
            dock.start_drag(0)
            dock.drag_to(centre)
            assert dock.order() == _order_with_dragged_at("a.desktop", slot)

    def test_grab_time_nudge_keeps_the_icon_in_its_slot(self):
        # Given the drag just started on the leftmost icon
        dock = _ReorderDock()
        centres = dock.slot_centres()
        dock.start_drag(0)
        # When the cursor moves one pixel right of the grab point
        dock.drag_to(centres[0] + 1)
        # Then the cursor has not crossed the next centre, so nothing reorders
        assert dock.order() == list(_DRAG_IDS)
        assert dock.dnd.drag_index == 0

    def test_drag_past_the_far_end_lands_on_the_last_slot(self):
        # Given the leftmost icon dragged past the rightmost slot's centre
        dock = _ReorderDock()
        centres = dock.slot_centres()
        dock.start_drag(0)
        # When / Then the icon takes the last slot instead of stopping one short
        dock.drag_to(centres[-1] + 200)
        assert dock.order() == _order_with_dragged_at("a.desktop", 3)
        assert dock.dnd.drag_index == 3

    def test_leftward_drag_crosses_each_slot_on_the_way_back(self):
        # Given the rightmost icon dragged leftwards 1px at a time
        dock = _ReorderDock()
        centres = dock.slot_centres()
        dock.start_drag(len(centres) - 1)
        transitions: dict[int, int] = {}
        previous = len(centres) - 1
        # When
        for cursor_x in range(int(centres[-1]) + 40, int(centres[0]) - 4, -1):
            dock.drag_to(cursor_x)
            index = dock.order().index("d.desktop")
            if index != previous:
                transitions[cursor_x] = index
                previous = index
        # Then it moves one slot left each time the cursor passes a centre
        assert transitions == {
            int(centres[2]) - 1: 2,
            int(centres[1]) - 1: 1,
            int(centres[0]) - 1: 0,
        }
