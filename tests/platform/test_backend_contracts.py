"""Tests for backend-neutral platform contracts."""

from docking.platform.backends.base import (
    ActionResult,
    PlatformCapabilities,
    Rect,
    WindowId,
    WindowSnapshot,
)
from docking.platform.running import RunningAppInfo, RunningWindowInfo


class TestWindowId:
    def test_x11_constructor_preserves_backend_and_xid(self):
        window_id = WindowId.x11(42)

        assert window_id.backend == "x11"
        assert window_id.value == 42
        assert str(window_id) == "x11:42"

    def test_distinguishes_same_value_from_different_backends(self):
        x11 = WindowId("x11", 7)
        wayland = WindowId("wayland-wlr", 7)

        assert x11 != wayland
        assert len({x11, wayland}) == 2


class TestRect:
    def test_overlaps_when_rectangles_intersect(self):
        assert Rect(0, 0, 20, 20).overlaps(Rect(10, 10, 20, 20))

    def test_does_not_overlap_when_edges_only_touch(self):
        assert not Rect(0, 0, 20, 20).overlaps(Rect(20, 0, 20, 20))


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
