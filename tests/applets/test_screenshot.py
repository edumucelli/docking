"""Tests for the screenshot applet."""

from unittest.mock import patch

from docking.applets.screenshot import (
    _TOOLS,
    ScreenshotApplet,
    Tool,
    _detect_tool,
    _run,
)

_MATE = Tool("mate-screenshot", [], ["-w"], ["-a"])
_GNOME = Tool("gnome-screenshot", [], ["-w"], ["-a"])
_XFCE = Tool("xfce4-screenshooter", ["-f"], ["-w"], ["-r"])
_SPECTACLE = Tool("spectacle", ["--fullscreen"], ["--activewindow"], ["--region"])
_FLAMESHOT = Tool("flameshot", ["full"], ["gui"], ["gui"])
_SCROT = Tool("scrot", [], ["-u"], ["-s"])


class TestTool:
    def test_all_tools_have_command(self):
        for tool in _TOOLS:
            assert tool.command

    def test_tools_order(self):
        commands = [t.command for t in _TOOLS]
        assert commands == [
            "mate-screenshot",
            "gnome-screenshot",
            "xfce4-screenshooter",
            "spectacle",
            "flameshot",
            "scrot",
        ]


class TestDetectTool:
    def test_returns_first_available(self):
        with patch(
            "docking.applets.screenshot.shutil.which",
            side_effect=[None, "/usr/bin/gnome-screenshot"],
        ):
            result = _detect_tool()
        assert result == _GNOME

    def test_returns_none_when_nothing_found(self):
        with patch("docking.applets.screenshot.shutil.which", return_value=None):
            assert _detect_tool() is None


class TestRun:
    def test_mate_screenshot_full(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_MATE, mode="full")
        p.assert_called_once_with(["mate-screenshot"], start_new_session=True)

    def test_mate_screenshot_window(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_MATE, mode="window")
        p.assert_called_once_with(["mate-screenshot", "-w"], start_new_session=True)

    def test_mate_screenshot_region(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_MATE, mode="region")
        p.assert_called_once_with(["mate-screenshot", "-a"], start_new_session=True)

    def test_gnome_screenshot_full(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_GNOME, mode="full")
        p.assert_called_once_with(["gnome-screenshot"], start_new_session=True)

    def test_gnome_screenshot_window(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_GNOME, mode="window")
        p.assert_called_once_with(["gnome-screenshot", "-w"], start_new_session=True)

    def test_xfce4_screenshooter_full(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_XFCE, mode="full")
        p.assert_called_once_with(["xfce4-screenshooter", "-f"], start_new_session=True)

    def test_xfce4_screenshooter_window(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_XFCE, mode="window")
        p.assert_called_once_with(["xfce4-screenshooter", "-w"], start_new_session=True)

    def test_xfce4_screenshooter_region(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_XFCE, mode="region")
        p.assert_called_once_with(["xfce4-screenshooter", "-r"], start_new_session=True)

    def test_spectacle_full(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_SPECTACLE, mode="full")
        p.assert_called_once_with(["spectacle", "--fullscreen"], start_new_session=True)

    def test_spectacle_window(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_SPECTACLE, mode="window")
        p.assert_called_once_with(
            ["spectacle", "--activewindow"], start_new_session=True
        )

    def test_spectacle_region(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_SPECTACLE, mode="region")
        p.assert_called_once_with(["spectacle", "--region"], start_new_session=True)

    def test_flameshot_full(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_FLAMESHOT, mode="full")
        p.assert_called_once_with(["flameshot", "full"], start_new_session=True)

    def test_flameshot_window(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_FLAMESHOT, mode="window")
        p.assert_called_once_with(["flameshot", "gui"], start_new_session=True)

    def test_flameshot_region(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_FLAMESHOT, mode="region")
        p.assert_called_once_with(["flameshot", "gui"], start_new_session=True)

    def test_scrot_appends_path(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_SCROT, mode="full")
        cmd = p.call_args[0][0]
        assert cmd[0] == "scrot"
        assert cmd[-1].endswith(".png")

    def test_scrot_window(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_SCROT, mode="window")
        cmd = p.call_args[0][0]
        assert cmd[0:2] == ["scrot", "-u"]
        assert cmd[-1].endswith(".png")

    def test_scrot_region(self):
        with patch("docking.applets.screenshot.subprocess.Popen") as p:
            _run(tool=_SCROT, mode="region")
        cmd = p.call_args[0][0]
        assert cmd[0:2] == ["scrot", "-s"]
        assert cmd[-1].endswith(".png")


class TestScreenshotApplet:
    def test_creates_with_icon(self):
        with patch("docking.applets.screenshot._detect_tool", return_value=_MATE):
            applet = ScreenshotApplet(48)
        assert applet.item.icon is not None
        assert applet.item.name == "Screenshot"

    def test_icon_renders_at_various_sizes(self):
        with patch("docking.applets.screenshot._detect_tool", return_value=_MATE):
            for size in [32, 48, 64]:
                applet = ScreenshotApplet(size)
                pixbuf = applet.create_icon(size)
                assert pixbuf is not None
                assert pixbuf.get_width() == size

    def test_menu_has_three_modes(self):
        with patch("docking.applets.screenshot._detect_tool", return_value=_MATE):
            applet = ScreenshotApplet(48)
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert labels == ["Full Screen", "Window", "Region"]

    def test_menu_empty_when_no_tool(self):
        with patch("docking.applets.screenshot._detect_tool", return_value=None):
            applet = ScreenshotApplet(48)
        assert applet.get_menu_items() == []

    def test_on_clicked_calls_popen(self):
        with patch("docking.applets.screenshot._detect_tool", return_value=_MATE):
            applet = ScreenshotApplet(48)
        with patch("docking.applets.screenshot.subprocess.Popen") as mock_popen:
            applet.on_clicked()
        mock_popen.assert_called_once_with(["mate-screenshot"], start_new_session=True)

    def test_on_clicked_noop_when_no_tool(self):
        with patch("docking.applets.screenshot._detect_tool", return_value=None):
            applet = ScreenshotApplet(48)
        with patch("docking.applets.screenshot.subprocess.Popen") as mock_popen:
            applet.on_clicked()
        mock_popen.assert_not_called()
