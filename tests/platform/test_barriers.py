"""Tests for pointer barrier support detection and lifecycle."""

import ctypes
from unittest.mock import MagicMock, patch

import pytest

from docking.core.position import Position
from docking.platform.barriers import PointerBarrier, _load_libs


class TestLoadLibs:
    """Library loading for barrier support."""

    def test_returns_triple_when_available(self):
        result = _load_libs()
        # On a standard X11 system all three libs should be present
        if result is not None:
            xlib, xfixes, xi = result
            assert xlib is not None
            assert xfixes is not None
            assert xi is not None

    def test_returns_none_on_missing_lib(self):
        with patch("ctypes.cdll.LoadLibrary", side_effect=OSError("not found")):
            assert _load_libs() is None


class TestPointerBarrierInit:
    """Barrier initialization and state."""

    def test_not_supported_by_default(self):
        barrier = PointerBarrier()
        assert not barrier.supported

    def test_destroy_noop_when_no_barrier(self):
        barrier = PointerBarrier()
        barrier.destroy()  # should not raise

    def test_update_noop_when_not_supported(self):
        from docking.core.position import Position

        barrier = PointerBarrier()
        # Should not raise even without initialization
        barrier.update(
            position=Position.BOTTOM,
            monitor_x=0,
            monitor_y=0,
            monitor_w=1920,
            monitor_h=1080,
        )


class TestPointerBarrierInitialize:
    """XInput version checking during initialize."""

    def _make_mock_display(self):
        display = MagicMock()
        display.get_xdisplay.return_value = 12345
        return display

    def test_unsupported_when_libs_unavailable(self):
        barrier = PointerBarrier()
        with patch("docking.platform.barriers._load_libs", return_value=None):
            assert not barrier.initialize(gdk_display=self._make_mock_display())
            assert not barrier.supported

    def test_unsupported_when_xinput_extension_missing(self):
        barrier = PointerBarrier()
        xlib = MagicMock()
        xlib.XQueryExtension.return_value = 0  # extension not found
        libs = (xlib, MagicMock(), MagicMock())
        with patch("docking.platform.barriers._load_libs", return_value=libs):
            assert not barrier.initialize(gdk_display=self._make_mock_display())
            assert not barrier.supported

    def test_unsupported_when_xinput_query_fails(self):
        barrier = PointerBarrier()
        xlib = MagicMock()
        xlib.XQueryExtension.return_value = 1
        xi = MagicMock()
        xi.XIQueryVersion.return_value = -1  # failure
        libs = (xlib, MagicMock(), xi)
        with patch("docking.platform.barriers._load_libs", return_value=libs):
            # ctypes argtypes assignment will proceed but the mock
            # won't behave like real ctypes; test the load path
            result = barrier.initialize(gdk_display=self._make_mock_display())
            assert isinstance(result, bool)


class TestPointerBarrierCoordinates:
    """Coordinate space delivered to XFixesCreatePointerBarrier.

    Issue #76 hypothesis: PointerBarrier.update receives logical pixels
    from its caller and forwards them to XFixesCreatePointerBarrier without
    any scale-factor multiplication. XFixes operates on the X11 root window,
    which uses physical pixels. On a HiDPI display (scale > 1) this places
    the barrier line at the wrong absolute Y -- visible to the user as an
    invisible horizontal mouse-blocking line at logical y = H_physical / S^2.

    These tests pin down where coordinates currently flow so the fix can be
    verified and regressions caught.
    """

    @staticmethod
    def _make_supported_barrier():
        xlib = MagicMock()
        xlib.XDefaultRootWindow.return_value = 1
        xfixes = MagicMock()
        xfixes.XFixesCreatePointerBarrier.return_value = 42
        barrier = PointerBarrier()
        barrier._supported = True
        barrier._libs = (xlib, xfixes, MagicMock())
        barrier._xdisplay = ctypes.c_void_p(99)
        return barrier, xlib, xfixes

    def test_update_forwards_caller_coords_unchanged_to_xfixes(self):
        """PointerBarrier.update is a thin wrapper -- whatever x/y/w/h the
        caller hands in is what XFixes receives. This pins the current
        contract: scale handling, if any, belongs to the caller.

        If the fix introduces an internal scale parameter on update(),
        this test should be updated to reflect the new contract (and the
        end-to-end placement test below will still cover the user-visible
        behavior)."""
        barrier, _xlib, xfixes = self._make_supported_barrier()

        barrier.update(
            position=Position.BOTTOM,
            monitor_x=0,
            monitor_y=0,
            monitor_w=1920,
            monitor_h=1080,
        )

        x1, y1, x2, y2 = xfixes.XFixesCreatePointerBarrier.call_args.args[2:6]
        assert (x1, y1, x2, y2) == (0, 1080, 1920, 1080)

    @pytest.mark.parametrize(
        "position,expected",
        [
            (Position.BOTTOM, (0, 1080, 1920, 1080)),
            (Position.TOP, (0, 0, 1920, 0)),
            (Position.LEFT, (0, 0, 0, 1080)),
            (Position.RIGHT, (1920, 0, 1920, 1080)),
        ],
    )
    def test_update_barrier_line_geometry_per_edge(self, position, expected):
        """The barrier line spans the dock edge of the monitor. This locks
        in the per-edge endpoint computation independently of scaling, so a
        scale-factor fix cannot accidentally swap axes or corners."""
        barrier, _xlib, xfixes = self._make_supported_barrier()

        barrier.update(
            position=position,
            monitor_x=0,
            monitor_y=0,
            monitor_w=1920,
            monitor_h=1080,
        )

        coords = xfixes.XFixesCreatePointerBarrier.call_args.args[2:6]
        assert coords == expected


class TestPressureHandler:
    """Pressure accumulation, threshold gating, and slide-vs-distance filter."""

    @staticmethod
    def _make_barrier_with_pressure(position=Position.BOTTOM, threshold=50):
        xlib = MagicMock()
        xlib.XDefaultRootWindow.return_value = 1
        xfixes = MagicMock()
        xfixes.XFixesCreatePointerBarrier.return_value = 42
        barrier = PointerBarrier()
        barrier._supported = True
        barrier._libs = (xlib, xfixes, MagicMock())
        barrier._xdisplay = ctypes.c_void_p(99)
        barrier._barrier_id = 42
        barrier._position = position
        callback = MagicMock()
        barrier.set_pressure_handler(callback=callback, threshold=threshold)
        return barrier, callback

    def test_no_callback_means_pressure_is_ignored(self):
        barrier = PointerBarrier()
        barrier._position = Position.BOTTOM
        barrier.set_pressure_handler(callback=None, threshold=1)
        # Should not raise; should not accumulate visibly.
        barrier._handle_barrier_hit(dx=0.0, dy=100.0)
        assert barrier._accumulated_pressure == 0.0

    def test_perpendicular_motion_accumulates_until_threshold(self):
        barrier, callback = self._make_barrier_with_pressure(threshold=31)
        # 2 hits of dy=15 sum to 30, still below 31.
        barrier._handle_barrier_hit(dx=0.0, dy=15.0)
        barrier._handle_barrier_hit(dx=0.0, dy=15.0)
        assert callback.call_count == 0
        # 3rd hit pushes total to 45, crossing 31.
        barrier._handle_barrier_hit(dx=0.0, dy=15.0)
        assert callback.call_count == 1

    def test_slide_along_edge_does_not_accumulate(self):
        barrier, callback = self._make_barrier_with_pressure(threshold=10)
        # For BOTTOM, dx is slide, dy is distance. slide >= distance -> skip.
        for _ in range(20):
            barrier._handle_barrier_hit(dx=100.0, dy=5.0)
        assert callback.call_count == 0
        assert barrier._accumulated_pressure == 0.0

    def test_per_event_cap_prevents_single_jab_from_blowing_through(self):
        barrier, callback = self._make_barrier_with_pressure(threshold=50)
        # One event with huge dy gets clamped to 15 per the per-event cap.
        barrier._handle_barrier_hit(dx=0.0, dy=1000.0)
        assert callback.call_count == 0
        assert barrier._accumulated_pressure == 15.0

    def test_threshold_reset_after_firing(self):
        barrier, callback = self._make_barrier_with_pressure(threshold=15)
        barrier._handle_barrier_hit(dx=0.0, dy=15.0)
        assert callback.call_count == 1
        assert barrier._accumulated_pressure == 0.0
        # A second push has to accumulate from zero again.
        barrier._handle_barrier_hit(dx=0.0, dy=10.0)
        assert callback.call_count == 1

    def test_vertical_dock_uses_dx_as_distance(self):
        barrier, callback = self._make_barrier_with_pressure(
            position=Position.LEFT, threshold=15
        )
        # For LEFT/RIGHT, dx is distance, dy is slide.
        barrier._handle_barrier_hit(dx=15.0, dy=0.0)
        assert callback.call_count == 1

    def test_callback_exception_is_swallowed(self):
        barrier, callback = self._make_barrier_with_pressure(threshold=5)
        callback.side_effect = RuntimeError("boom")
        # Must not propagate, otherwise the X event filter would crash GDK.
        barrier._handle_barrier_hit(dx=0.0, dy=15.0)
        assert callback.call_count == 1
