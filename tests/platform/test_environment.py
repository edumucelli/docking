"""Tests for desktop environment detection and tweaks."""

import logging
from unittest.mock import patch

from gi.repository import GdkX11

from docking.platform.environment import (
    Desktop,
    _check_compositor,
    _parse_desktop,
    detect_desktop,
)


class TestParseDesktop:
    """Parsing XDG desktop strings into Desktop flags."""

    def test_single_known_value(self):
        assert _parse_desktop("mate") == Desktop.MATE

    def test_case_insensitive(self):
        assert _parse_desktop("XFCE") == Desktop.XFCE

    def test_semicolon_separated(self):
        result = _parse_desktop("ubuntu:GNOME;ubuntu")
        # "ubuntu:GNOME" is unknown, "ubuntu" maps to UBUNTU
        assert result & Desktop.UBUNTU

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
