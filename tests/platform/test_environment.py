"""Tests for desktop environment detection and tweaks."""

import logging
from unittest.mock import patch

from docking.platform.environment import (
    Desktop,
    _check_compositor,
    _parse_desktop,
    detect_desktop,
    is_flatpak,
    is_gnome_session,
    is_kde_session,
    is_mate_session,
    is_wayland_session,
    is_x11_backend,
    is_xwayland_session,
)


class TestParseDesktop:
    """Parsing XDG desktop strings into Desktop flags."""

    def test_single_known_value(self):
        assert _parse_desktop("mate") == Desktop.MATE

    def test_case_insensitive(self):
        assert _parse_desktop("XFCE") == Desktop.XFCE

    def test_desktop_list_separators(self):
        result = _parse_desktop("ubuntu:GNOME;ubuntu")
        assert result & Desktop.UBUNTU
        assert result & Desktop.GNOME

    def test_xdg_current_desktop_multi(self):
        # Real-world: XDG_CURRENT_DESKTOP=MATE;MATE
        result = _parse_desktop("MATE;MATE")
        assert result == Desktop.MATE

    def test_unknown_returns_unknown(self):
        assert _parse_desktop("some-obscure-wm") == Desktop.UNKNOWN

    def test_empty_returns_unknown(self):
        assert _parse_desktop("") == Desktop.UNKNOWN

    def test_cinnamon_alias(self):
        assert _parse_desktop("x-cinnamon") == Desktop.CINNAMON

    def test_xubuntu_maps_to_xfce(self):
        assert _parse_desktop("xubuntu") == Desktop.XFCE

    def test_gnome_variants(self):
        for variant in ("gnome", "gnome-xorg", "gnome-classic", "gnome-flashback"):
            assert _parse_desktop(variant) == Desktop.GNOME

    def test_lxqt_maps_to_lxde(self):
        assert _parse_desktop("lxqt") == Desktop.LXDE


class TestDetectDesktop:
    """Detection priority: XDG_SESSION_DESKTOP > XDG_CURRENT_DESKTOP > DESKTOP_SESSION."""

    def test_session_desktop_takes_priority(self):
        env = {
            "XDG_SESSION_DESKTOP": "mate",
            "XDG_CURRENT_DESKTOP": "xfce",
        }
        with patch.dict("os.environ", env, clear=True):
            assert detect_desktop() == Desktop.MATE

    def test_falls_back_to_current_desktop(self):
        env = {"XDG_CURRENT_DESKTOP": "KDE"}
        with patch.dict("os.environ", env, clear=True):
            assert detect_desktop() == Desktop.KDE

    def test_falls_back_to_desktop_session(self):
        env = {"DESKTOP_SESSION": "cinnamon"}
        with patch.dict("os.environ", env, clear=True):
            assert detect_desktop() == Desktop.CINNAMON

    def test_skips_unknown_session_desktop(self):
        env = {
            "XDG_SESSION_DESKTOP": "unknown-wm",
            "XDG_CURRENT_DESKTOP": "MATE",
        }
        with patch.dict("os.environ", env, clear=True):
            assert detect_desktop() == Desktop.MATE

    def test_no_env_returns_unknown(self):
        with patch.dict("os.environ", {}, clear=True):
            assert detect_desktop() == Desktop.UNKNOWN

    def test_gnome_session_helper_includes_ubuntu(self):
        assert is_gnome_session(desktop=Desktop.GNOME) is True
        assert is_gnome_session(desktop=Desktop.UBUNTU) is True
        assert is_gnome_session(desktop=Desktop.MATE) is False

    def test_mate_session_helper(self):
        assert is_mate_session(desktop=Desktop.MATE) is True
        assert is_mate_session(desktop=Desktop.GNOME) is False

    def test_kde_session_helper(self):
        assert is_kde_session(desktop=Desktop.KDE) is True
        assert is_kde_session(desktop=Desktop.GNOME) is False


class TestSessionBackendDetection:
    def test_is_wayland_session_true(self):
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=True):
            assert is_wayland_session() is True

    def test_is_wayland_session_false(self):
        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}, clear=True):
            assert is_wayland_session() is False

    def test_is_flatpak_checks_runtime_marker(self):
        with patch("docking.platform.environment.Path.exists", return_value=True):
            assert is_flatpak() is True

        with patch("docking.platform.environment.Path.exists", return_value=False):
            assert is_flatpak() is False

    def test_is_x11_backend_true_for_x11_display(self):
        display_cls = type(
            "X11Display",
            (),
            {"__module__": "gi.repository.GdkX11"},
        )

        assert is_x11_backend(display=display_cls()) is True

    def test_is_x11_backend_false_without_x11_display(self):
        assert is_x11_backend(display=object()) is False

    def test_is_xwayland_session_true(self):
        display_cls = type(
            "X11Display",
            (),
            {"__module__": "gi.repository.GdkX11"},
        )

        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland"}, clear=True):
            assert is_xwayland_session(display=display_cls()) is True

    def test_is_xwayland_session_false_in_native_x11_session(self):
        display_cls = type(
            "X11Display",
            (),
            {"__module__": "gi.repository.GdkX11"},
        )

        with patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11"}, clear=True):
            assert is_xwayland_session(display=display_cls()) is False


class TestUsesMonitorGeometry:
    """Known DEs use full monitor geometry; unknown uses workarea."""

    def test_known_des_use_geometry(self):
        for de in (
            Desktop.GNOME,
            Desktop.MATE,
            Desktop.XFCE,
            Desktop.KDE,
            Desktop.CINNAMON,
            Desktop.UBUNTU,
        ):
            assert de.uses_monitor_geometry, f"{de} should use monitor geometry"

    def test_unknown_uses_workarea(self):
        assert not Desktop.UNKNOWN.uses_monitor_geometry

    def test_pantheon_uses_workarea(self):
        assert not Desktop.PANTHEON.uses_monitor_geometry

    def test_combined_flags(self):
        combined = Desktop.MATE | Desktop.GNOME
        assert combined.uses_monitor_geometry


class TestCompositorCheck:
    def test_logs_warning_when_probe_fails(self, caplog):
        from gi.repository import GdkX11

        class _Display:
            def get_default_screen(self):
                return 0

            def get_xdisplay(self):
                return object()

        with (
            patch.object(GdkX11.X11Display, "get_default", return_value=_Display()),
            patch("ctypes.cdll.LoadLibrary", side_effect=RuntimeError("boom")),
            caplog.at_level(logging.WARNING, logger="docking.environment"),
        ):
            _check_compositor()

        assert "failed to check compositor status" in caplog.text
