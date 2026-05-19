"""Tests for the screenshot applet."""

import subprocess
from unittest.mock import MagicMock, patch

import docking.applets.screenshot.state as screenshot_state
from docking.applets.screenshot.applet import ScreenshotApplet
from docking.applets.screenshot.state import _TOOLS, Tool, _detect_tool, _run

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
    def test_prefers_portal_first_in_wayland_session(self):
        with (
            patch.object(screenshot_state, "is_wayland_session", return_value=True),
            patch.object(screenshot_state, "_portal_available", return_value=True),
            patch("docking.applets.screenshot.state.shutil.which") as which,
        ):
            result = _detect_tool()

        assert result == screenshot_state._PORTAL_TOOL
        which.assert_not_called()

    def test_returns_first_available(self):
        with (
            patch.object(screenshot_state, "is_wayland_session", return_value=False),
            patch.object(screenshot_state, "_portal_available", return_value=False),
            patch(
                "docking.applets.screenshot.state.shutil.which",
                side_effect=[None, "/usr/bin/gnome-screenshot"],
            ),
        ):
            result = _detect_tool()
        assert result == _GNOME

    def test_returns_none_when_nothing_found(self):
        with (
            patch.object(screenshot_state, "is_wayland_session", return_value=False),
            patch.object(screenshot_state, "_portal_available", return_value=False),
            patch("docking.applets.screenshot.state.shutil.which", return_value=None),
        ):
            assert _detect_tool() is None

    def test_falls_back_to_portal_when_cli_tools_missing(self):
        with (
            patch.object(screenshot_state, "is_wayland_session", return_value=False),
            patch.object(screenshot_state, "is_flatpak", return_value=False),
            patch.object(screenshot_state, "_portal_available", side_effect=[True]),
            patch("docking.applets.screenshot.state.shutil.which", return_value=None),
        ):
            assert _detect_tool() == screenshot_state._PORTAL_TOOL

    def test_flatpak_falls_back_to_host_screenshot_tool(self):
        with (
            patch.object(screenshot_state, "is_wayland_session", return_value=False),
            patch.object(screenshot_state, "is_flatpak", return_value=True),
            patch.object(screenshot_state, "_portal_available", return_value=False),
            patch("docking.applets.screenshot.state.shutil.which", return_value=None),
            patch.object(
                screenshot_state.flatpak,
                "spawn_path",
                return_value="/usr/bin/flatpak-spawn",
            ),
            patch.object(
                screenshot_state.flatpak,
                "host_command_available",
                return_value=True,
            ) as available,
        ):
            result = _detect_tool()

        assert result == Tool("mate-screenshot", [], ["-w"], ["-a"], "flatpak-host")
        available.assert_called_once_with("mate-screenshot")


class TestPortal:
    def test_portal_available_requires_screenshot_interface(self):
        with patch("docking.applets.screenshot.state.shutil.which", return_value=None):
            assert screenshot_state._portal_available() is False

        with (
            patch(
                "docking.applets.screenshot.state.shutil.which", return_value="gdbus"
            ),
            patch(
                "docking.applets.screenshot.state.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["gdbus"],
                    returncode=0,
                    stdout="interface org.freedesktop.portal.Screenshot {",
                ),
            ),
        ):
            assert screenshot_state._portal_available() is True

        with (
            patch(
                "docking.applets.screenshot.state.shutil.which", return_value="gdbus"
            ),
            patch(
                "docking.applets.screenshot.state.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["gdbus"],
                    returncode=0,
                    stdout="interface org.freedesktop.portal.FileChooser {",
                ),
            ),
        ):
            assert screenshot_state._portal_available() is False

        with (
            patch(
                "docking.applets.screenshot.state.shutil.which", return_value="gdbus"
            ),
            patch(
                "docking.applets.screenshot.state.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=["gdbus"],
                    returncode=1,
                    stdout="",
                ),
            ),
        ):
            assert screenshot_state._portal_available() is False

        with (
            patch(
                "docking.applets.screenshot.state.shutil.which", return_value="gdbus"
            ),
            patch(
                "docking.applets.screenshot.state.subprocess.run",
                MagicMock(side_effect=subprocess.TimeoutExpired("gdbus", 1)),
            ),
        ):
            assert screenshot_state._portal_available() is False

    def test_portal_args_full_and_interactive(self):
        full = screenshot_state._portal_args(mode="full")
        region = screenshot_state._portal_args(mode="region")

        assert "interactive': <false>" in full[-1]
        assert "interactive': <true>" in region[-1]


class TestRun:
    def test_mate_screenshot_full(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_MATE, mode="full")
        p.assert_called_once_with(["mate-screenshot"], start_new_session=True)

    def test_mate_screenshot_window(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_MATE, mode="window")
        p.assert_called_once_with(["mate-screenshot", "-w"], start_new_session=True)

    def test_mate_screenshot_region(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_MATE, mode="region")
        p.assert_called_once_with(["mate-screenshot", "-a"], start_new_session=True)

    def test_mate_screenshot_full_with_delay(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_MATE, mode="full", delay_seconds=5)
        p.assert_called_once_with(
            ["mate-screenshot", "-d", "5"], start_new_session=True
        )

    def test_gnome_screenshot_full(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_GNOME, mode="full")
        p.assert_called_once_with(["gnome-screenshot"], start_new_session=True)

    def test_gnome_screenshot_window(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_GNOME, mode="window")
        p.assert_called_once_with(["gnome-screenshot", "-w"], start_new_session=True)

    def test_xfce4_screenshooter_full(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_XFCE, mode="full")
        p.assert_called_once_with(["xfce4-screenshooter", "-f"], start_new_session=True)

    def test_xfce4_screenshooter_window(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_XFCE, mode="window")
        p.assert_called_once_with(["xfce4-screenshooter", "-w"], start_new_session=True)

    def test_xfce4_screenshooter_region(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_XFCE, mode="region")
        p.assert_called_once_with(["xfce4-screenshooter", "-r"], start_new_session=True)

    def test_spectacle_full(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_SPECTACLE, mode="full")
        p.assert_called_once_with(["spectacle", "--fullscreen"], start_new_session=True)

    def test_spectacle_window(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_SPECTACLE, mode="window")
        p.assert_called_once_with(
            ["spectacle", "--activewindow"], start_new_session=True
        )

    def test_spectacle_region(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_SPECTACLE, mode="region")
        p.assert_called_once_with(["spectacle", "--region"], start_new_session=True)

    def test_flameshot_full(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_FLAMESHOT, mode="full")
        p.assert_called_once_with(["flameshot", "full"], start_new_session=True)

    def test_flameshot_full_with_delay(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_FLAMESHOT, mode="full", delay_seconds=3)
        p.assert_called_once_with(
            ["flameshot", "full", "--delay", "3000"], start_new_session=True
        )

    def test_flameshot_window(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_FLAMESHOT, mode="window")
        p.assert_called_once_with(["flameshot", "gui"], start_new_session=True)

    def test_flameshot_region(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_FLAMESHOT, mode="region")
        p.assert_called_once_with(["flameshot", "gui"], start_new_session=True)

    def test_scrot_appends_path(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_SCROT, mode="full")
        cmd = p.call_args[0][0]
        assert cmd[0] == "scrot"
        assert cmd[-1].endswith(".png")

    def test_scrot_window(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_SCROT, mode="window")
        cmd = p.call_args[0][0]
        assert cmd[0:2] == ["scrot", "-u"]
        assert cmd[-1].endswith(".png")

    def test_scrot_region(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_SCROT, mode="region")
        cmd = p.call_args[0][0]
        assert cmd[0:2] == ["scrot", "-s"]
        assert cmd[-1].endswith(".png")

    def test_scrot_region_with_delay(self):
        with patch("docking.applets.screenshot.state.subprocess.Popen") as p:
            _run(tool=_SCROT, mode="region", delay_seconds=7)
        cmd = p.call_args[0][0]
        assert cmd[0:4] == ["scrot", "-s", "-d", "7"]
        assert cmd[-1].endswith(".png")

    def test_delay_args_for_other_tools_and_zero(self):
        assert screenshot_state._delay_args(tool=_MATE, delay_seconds=0) == []
        assert screenshot_state._delay_args(tool=_XFCE, delay_seconds=3) == ["-d", "3"]
        assert screenshot_state._delay_args(tool=_SPECTACLE, delay_seconds=3) == [
            "--delay",
            "3",
        ]
        assert (
            screenshot_state._delay_args(
                tool=Tool("custom", [], [], []),
                delay_seconds=3,
            )
            == []
        )

    def test_portal_run_and_delayed_launch(self, monkeypatch):
        launched: list[list[str]] = []
        monkeypatch.setattr(
            screenshot_state.subprocess,
            "Popen",
            lambda cmd, start_new_session=True: launched.append(list(cmd)),
        )

        cmd = _run(tool=screenshot_state._PORTAL_TOOL, mode="window")

        assert cmd[:2] == ["gdbus", "call"]
        assert launched == [cmd]

        timers = []

        class _Timer:
            def __init__(self, delay, fn, args, kwargs):
                self.delay = delay
                self.fn = fn
                self.args = args
                self.kwargs = kwargs
                self.daemon = False

            def start(self):
                timers.append(self)

        monkeypatch.setattr(screenshot_state.threading, "Timer", _Timer)
        screenshot_state._launch(cmd=["tool"], delay_seconds=2)

        assert timers[0].delay == 2
        assert timers[0].daemon is True

    def test_flatpak_host_run_prefixes_host_spawn(self):
        tool = Tool("mate-screenshot", [], ["-w"], ["-a"], "flatpak-host")
        with (
            patch.object(
                screenshot_state.flatpak,
                "spawn_path",
                return_value="/usr/bin/flatpak-spawn",
            ),
            patch("docking.applets.screenshot.state.subprocess.Popen") as p,
        ):
            _run(tool=tool, mode="region", delay_seconds=3)
        p.assert_called_once_with(
            [
                "/usr/bin/flatpak-spawn",
                "--host",
                "env",
                "-u",
                "GIO_USE_VFS",
                "-u",
                "GI_TYPELIB_PATH",
                "-u",
                "GSETTINGS_SCHEMA_DIR",
                "-u",
                "XDG_DATA_DIRS",
                "mate-screenshot",
                "-a",
                "-d",
                "3",
            ],
            start_new_session=True,
        )


class TestScreenshotApplet:
    def test_creates_with_icon(self):
        with patch(
            "docking.applets.screenshot.applet._detect_tool", return_value=_MATE
        ):
            applet = ScreenshotApplet(48)
        assert applet.item.icon is not None
        assert applet.item.name == "Screenshot"

    def test_icon_renders_at_various_sizes(self):
        with patch(
            "docking.applets.screenshot.applet._detect_tool", return_value=_MATE
        ):
            for size in [32, 48, 64]:
                applet = ScreenshotApplet(size)
                pixbuf = applet.create_icon(size)
                assert pixbuf is not None
                assert pixbuf.get_width() == size

    def test_menu_has_modes_and_timed_entries(self):
        with patch(
            "docking.applets.screenshot.applet._detect_tool", return_value=_MATE
        ):
            applet = ScreenshotApplet(48)
        labels = [mi.get_label() for mi in applet.get_menu_items() if mi.get_label()]
        assert labels == [
            "Full Screen",
            "Window",
            "Region",
            "Full Screen in 3s",
            "Full Screen in 5s",
            "Full Screen in 7s",
            "Full Screen in 9s",
        ]

    def test_menu_empty_when_no_tool(self):
        with patch("docking.applets.screenshot.applet._detect_tool", return_value=None):
            applet = ScreenshotApplet(48)
        assert applet.get_menu_items() == []

    def test_on_clicked_calls_popen(self):
        with patch(
            "docking.applets.screenshot.applet._detect_tool", return_value=_MATE
        ):
            applet = ScreenshotApplet(48)
        with patch("docking.applets.screenshot.state.subprocess.Popen") as mock_popen:
            applet.on_clicked()
        mock_popen.assert_called_once_with(["mate-screenshot"], start_new_session=True)

    def test_on_clicked_noop_when_no_tool(self):
        with patch("docking.applets.screenshot.applet._detect_tool", return_value=None):
            applet = ScreenshotApplet(48)
        with patch("docking.applets.screenshot.state.subprocess.Popen") as mock_popen:
            applet.on_clicked()
        mock_popen.assert_not_called()

    def test_run_mode_forwards_delay(self):
        with patch(
            "docking.applets.screenshot.applet._detect_tool", return_value=_MATE
        ):
            applet = ScreenshotApplet(48)
        with patch("docking.applets.screenshot.applet._run") as mock_run:
            applet._run_mode(mode="full", delay_seconds=9)
        mock_run.assert_called_once_with(tool=_MATE, mode="full", delay_seconds=9)
