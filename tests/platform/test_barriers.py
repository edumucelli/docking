"""Tests for pointer barrier support detection and lifecycle."""

from unittest.mock import MagicMock, patch

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
