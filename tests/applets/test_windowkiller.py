"""Tests for the Window Killer applet."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.applets.windowkiller import WindowKillerApplet
from docking.applets.windowkiller.state import kill_pid


class TestKillPid:
    def test_returns_false_for_zero(self):
        assert kill_pid(pid=0) is False

    def test_returns_false_for_negative(self):
        assert kill_pid(pid=-1) is False

    def test_returns_false_on_os_error(self, monkeypatch):
        monkeypatch.setattr("os.kill", MagicMock(side_effect=OSError("no process")))
        assert kill_pid(pid=99999) is False

    def test_returns_true_on_success(self, monkeypatch):
        killed = MagicMock()
        monkeypatch.setattr("os.kill", killed)
        assert kill_pid(pid=1234) is True
        killed.assert_called_once()


class TestAppletCreation:
    def test_creates_with_icon(self):
        applet = WindowKillerApplet(48)
        assert applet.item.icon is not None

    def test_tooltip(self):
        applet = WindowKillerApplet(48)
        applet.refresh_tooltip()
        assert "kill" in applet.item.name.lower()

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = WindowKillerApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None


class TestAppletOverlay:
    def test_on_clicked_starts_pick(self, monkeypatch):
        applet = WindowKillerApplet(48)
        pick = MagicMock()
        monkeypatch.setattr(applet, "_start_pick", pick)
        applet.on_clicked()
        pick.assert_called_once()

    def test_stop_dismisses_overlay(self):
        applet = WindowKillerApplet(48)
        overlay = MagicMock()
        applet._overlay = overlay
        applet.stop()
        overlay.destroy.assert_called_once()
        assert applet._overlay is None

    def test_dismiss_without_overlay_is_safe(self):
        applet = WindowKillerApplet(48)
        applet._dismiss_overlay()  # should not raise
