"""Tests for backend-neutral platform contracts."""

import pytest

from docking.platform.applications.running import RunningAppInfo, RunningWindowInfo
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    PlatformCapabilities,
    Rect,
    SessionBackend,
    SurfaceService,
    WindowId,
    WindowService,
    WindowSnapshot,
)


class TestWindowId:
    def test_x11_constructor_preserves_backend_and_xid(self):
        window_id = WindowId.x11(42)

        assert window_id.backend is DisplayServer.X11
        assert window_id.value == 42
        assert str(window_id) == "x11:42"

    def test_distinguishes_same_value_from_different_backends(self):
        x11 = WindowId(DisplayServer.X11, 7)
        wayland = WindowId(DisplayServer.WAYLAND, 7)

        assert x11 != wayland
        assert len({x11, wayland}) == 2


class TestRect:
    def test_overlaps_when_rectangles_intersect(self):
        assert Rect(0, 0, 20, 20).overlaps(Rect(10, 10, 20, 20))

    def test_does_not_overlap_when_edges_only_touch(self):
        assert not Rect(0, 0, 20, 20).overlaps(Rect(20, 0, 20, 20))

    def test_device_to_logical_is_unchanged_at_scale_one(self):
        rect = Rect(61, 81, 601, 401)

        assert rect.device_to_logical(1) == rect

    def test_device_to_logical_divides_by_the_scale(self):
        assert Rect(60, 80, 600, 400).device_to_logical(2) == Rect(30, 40, 300, 200)

    def test_device_to_logical_covers_the_rect_on_odd_values(self):
        # Origin rounds down, far edges round up: 61..661 -> 30..331.
        assert Rect(61, 81, 600, 400).device_to_logical(2) == Rect(30, 40, 301, 201)

    def test_device_to_logical_floors_negative_origins(self):
        assert Rect(-61, -3, 100, 10).device_to_logical(2) == Rect(-31, -2, 51, 6)


class TestPlatformCapabilities:
    def test_defaults_to_no_capabilities(self):
        capabilities = PlatformCapabilities()

        assert not capabilities.tracks_windows
        assert not capabilities.supports_any_overlap

    def test_supports_any_overlap_when_one_overlap_mode_exists(self):
        capabilities = PlatformCapabilities(supports_overlap_active=True)

        assert capabilities.supports_any_overlap


class TestWindowSnapshot:
    def test_defaults_are_safe_for_unsupported_actions(self):
        snapshot = WindowSnapshot(
            id=WindowId.x11(1),
            desktop_id="firefox.desktop",
        )

        assert snapshot.title == "Window"
        assert not snapshot.active
        assert not snapshot.can_activate
        assert snapshot.geometry is None


class TestRunningAppInfo:
    def test_preserves_xids_and_window_ids(self):
        first = object()
        second = object()

        running = RunningAppInfo.from_windows(
            [
                RunningWindowInfo(
                    desktop_id="firefox.desktop",
                    xid=1,
                    window_id=WindowId.x11(1),
                    active=False,
                    urgent=False,
                    window=first,
                ),
                RunningWindowInfo(
                    desktop_id="firefox.desktop",
                    xid=2,
                    window_id=WindowId.x11(2),
                    active=True,
                    urgent=True,
                    window=second,
                ),
            ]
        )

        assert running.count == 2
        assert running.active is True
        assert running.urgent is True
        assert running.windows == (first, second)
        assert running.xids == (1, 2)
        assert running.window_ids == (WindowId.x11(1), WindowId.x11(2))


class TestActionResult:
    def test_only_ok_succeeds(self):
        assert ActionResult.OK.succeeded
        assert not ActionResult.UNSUPPORTED.succeeded
        assert not ActionResult.NOT_FOUND.succeeded
        assert not ActionResult.FAILED.succeeded


class TestBackendContracts:
    def test_service_contracts_are_nominal_abstract_bases(self):
        for cls in (WindowService, SurfaceService, SessionBackend):
            with pytest.raises(TypeError):
                cls()
