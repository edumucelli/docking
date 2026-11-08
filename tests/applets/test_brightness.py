"""Tests for the brightness applet."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

import docking.applets.brightness.implementations.linux.backlight as native_brightness_backlight
import docking.applets.brightness.implementations.unix.backend as brightness_state
import docking.applets.brightness.implementations.unix.backend as native_brightness
from docking.applets.brightness.applet import BrightnessApplet
from docking.applets.brightness.backend import BrightnessBackend
from docking.applets.brightness.implementations.linux.backlight import (
    LinuxBacklightSource,
)
from docking.applets.brightness.implementations.unix.backend import (
    Backend,
    detect_output,
    get_brightness,
)
from docking.applets.brightness.state import BrightnessState, brightness_icon_name
from docking.core.config import Config
from tests.platform_factories import TestAppletBackendFactories, make_applet_platform


class _ImmediateWorker:
    def __init__(self, **_kwargs) -> None:
        pass

    def run(self, *, fn, on_result=None, on_error=None, **_kwargs) -> None:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)

    def run_guarded(self, *, fn, on_result=None, on_error=None, **_kwargs) -> bool:
        self.run(fn=fn, on_result=on_result, on_error=on_error)
        return True


class TestBrightnessIconName:
    def test_low(self):
        assert "low" in brightness_icon_name(brightness=0.2)

    def test_medium(self):
        assert "medium" in brightness_icon_name(brightness=0.5)

    def test_high(self):
        name = brightness_icon_name(brightness=0.9)
        assert "low" not in name and "medium" not in name


class TestDetectOutput:
    def test_native_backlight_is_available_without_xrandr(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            native_brightness,
            "is_wayland_session",
            lambda: True,
        )
        monkeypatch.setattr(
            native_brightness_backlight,
            "_BACKLIGHT_DIR",
            tmp_path,
        )
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: None,
        )
        monkeypatch.setattr(
            native_brightness.shutil,
            "which",
            lambda _cmd: None,
        )
        backlight = tmp_path / "panel"
        backlight.mkdir()
        (backlight / "brightness").write_text("60\n")
        (backlight / "max_brightness").write_text("100\n")

        backend = detect_output(backlight=LinuxBacklightSource())

        assert backend is not None
        assert get_brightness(backend, backlight=LinuxBacklightSource()) == 0.6

    def test_selects_primary_output_even_when_it_is_not_first(self, monkeypatch):
        output = (
            "Monitors: 2\n"
            " 0: +HDMI-1 1920/480x1080/270+0+0  HDMI-1\n"
            " 1: +*DP-1 1920/480x1080/270+1920+0  DP-1\n"
        )
        monkeypatch.setattr(
            native_brightness,
            "is_wayland_session",
            lambda: False,
        )
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: output,
        )
        monkeypatch.setattr(
            native_brightness_backlight,
            "_find_sysfs_backlight",
            lambda: None,
        )
        monkeypatch.setattr(
            native_brightness.shutil,
            "which",
            lambda _cmd: None,
        )

        assert detect_output(backlight=LinuxBacklightSource()).output == "DP-1"

    def test_parses_xrandr_listmonitors(self, monkeypatch):
        output = "Monitors: 1\n 0: +*HDMI-1 1920/480x1080/270+0+0  HDMI-1\n"
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: output,
        )
        monkeypatch.setattr(
            native_brightness_backlight,
            "_find_sysfs_backlight",
            lambda: None,
        )
        monkeypatch.setattr(
            native_brightness.shutil,
            "which",
            lambda _cmd: None,
        )
        result = detect_output(backlight=LinuxBacklightSource())
        assert result == Backend(output="HDMI-1", sysfs=None, brightnessctl=None)

    def test_includes_sysfs_when_available(self, monkeypatch, tmp_path):
        output = "Monitors: 1\n 0: +*eDP-1 1920/300x1080/170+0+0  eDP-1\n"
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: output,
        )
        monkeypatch.setattr(
            native_brightness_backlight,
            "_find_sysfs_backlight",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            native_brightness.shutil,
            "which",
            lambda _cmd: None,
        )
        result = detect_output(backlight=LinuxBacklightSource())
        assert result == Backend(output="eDP-1", sysfs=tmp_path, brightnessctl=None)

    def test_includes_brightnessctl_when_available(self, monkeypatch):
        output = "Monitors: 1\n 0: +*eDP-1 1920/300x1080/170+0+0  eDP-1\n"
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: output,
        )
        monkeypatch.setattr(
            native_brightness_backlight,
            "_find_sysfs_backlight",
            lambda: None,
        )
        monkeypatch.setattr(
            native_brightness.shutil,
            "which",
            lambda cmd: "/usr/bin/brightnessctl",
        )
        result = detect_output(backlight=LinuxBacklightSource())
        assert result == Backend(
            output="eDP-1", sysfs=None, brightnessctl="/usr/bin/brightnessctl"
        )

    def test_returns_none_on_failure(self, monkeypatch):
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: None,
        )
        assert detect_output(backlight=LinuxBacklightSource()) is None

    def test_returns_none_when_line_has_too_few_parts(self, monkeypatch):
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: "Monitors: 1\n 0:\n",
        )
        assert detect_output(backlight=LinuxBacklightSource()) is None


class TestGetBrightness:
    def test_output_name_must_match_exactly(self, monkeypatch):
        output = (
            "eDP-1 connected primary 1920x1080\n\tBrightness: 0.25\n"
            "DP-1 connected 1920x1080\n\tBrightness: 0.75\n"
        )
        monkeypatch.setattr(
            native_brightness,
            "is_wayland_session",
            lambda: False,
        )
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: output,
        )
        assert (
            get_brightness(Backend(output="DP-1"), backlight=LinuxBacklightSource())
            == 0.75
        )

    def test_prefers_sysfs_when_available_on_wayland(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            native_brightness,
            "is_wayland_session",
            lambda: True,
        )
        (tmp_path / "brightness").write_text("150\n")
        (tmp_path / "max_brightness").write_text("200\n")
        backend = Backend(output="eDP-1", sysfs=tmp_path)
        assert get_brightness(backend=backend, backlight=LinuxBacklightSource()) == 0.75

    def test_prefers_xrandr_when_available_on_x11(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            native_brightness,
            "is_wayland_session",
            lambda: False,
        )
        (tmp_path / "brightness").write_text("150\n")
        (tmp_path / "max_brightness").write_text("200\n")
        output = (
            "HDMI-1 connected primary 1920x1080\n"
            "\tBrightness: 0.40\n"
            "\tGamma: 1.0:1.0:1.0\n"
        )
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: output,
        )
        backend = Backend(output="HDMI-1", sysfs=tmp_path)
        assert get_brightness(backend=backend, backlight=LinuxBacklightSource()) == 0.40

    def test_sysfs_handles_read_error(self, monkeypatch, tmp_path):
        # Given - missing files
        monkeypatch.setattr(
            native_brightness,
            "is_wayland_session",
            lambda: True,
        )
        backend = Backend(output="eDP-1", sysfs=tmp_path)
        # When / Then
        assert get_brightness(backend=backend, backlight=LinuxBacklightSource()) is None

    def test_falls_back_to_xrandr_without_sysfs(self, monkeypatch):
        # Given
        output = (
            "HDMI-1 connected primary 1920x1080\n"
            "\tBrightness: 0.75\n"
            "\tGamma: 1.0:1.0:1.0\n"
        )
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: output,
        )
        backend = Backend(output="HDMI-1", sysfs=None)
        # When / Then
        assert get_brightness(backend=backend, backlight=LinuxBacklightSource()) == 0.75

    def test_falls_back_to_sysfs_on_x11_when_xrandr_missing(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            native_brightness,
            "is_wayland_session",
            lambda: False,
        )
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: None,
        )
        (tmp_path / "brightness").write_text("150\n")
        (tmp_path / "max_brightness").write_text("200\n")
        backend = Backend(output="eDP-1", sysfs=tmp_path)
        assert get_brightness(backend=backend, backlight=LinuxBacklightSource()) == 0.75

    def test_xrandr_returns_none_on_failure(self, monkeypatch):
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: None,
        )
        assert (
            get_brightness(
                backend=Backend(output="HDMI-1"), backlight=LinuxBacklightSource()
            )
            is None
        )

    def test_xrandr_returns_none_when_output_not_found(self, monkeypatch):
        output = "DP-1 connected\n\tBrightness: 0.55\n"
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: output,
        )
        assert (
            get_brightness(
                backend=Backend(output="HDMI-1"), backlight=LinuxBacklightSource()
            )
            is None
        )


class TestFindSysfsBacklight:
    def test_finds_backlight_dir(self, monkeypatch, tmp_path):
        # Given
        bl = tmp_path / "intel_backlight"
        bl.mkdir()
        (bl / "brightness").write_text("100\n")
        (bl / "max_brightness").write_text("200\n")
        monkeypatch.setattr(
            native_brightness_backlight,
            "_BACKLIGHT_DIR",
            tmp_path,
        )
        # When / Then
        assert native_brightness_backlight._find_sysfs_backlight() == bl

    def test_returns_none_when_no_backlight(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            native_brightness_backlight,
            "_BACKLIGHT_DIR",
            tmp_path,
        )
        assert native_brightness_backlight._find_sysfs_backlight() is None

    def test_returns_none_when_dir_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            native_brightness_backlight,
            "_BACKLIGHT_DIR",
            tmp_path / "nonexistent",
        )
        assert native_brightness_backlight._find_sysfs_backlight() is None


class TestBrightnessStateHelpers:
    def test_run_helper(self, monkeypatch):
        class _Proc:
            def __init__(self, code=0, out=""):
                self.returncode = code
                self.stdout = out

        monkeypatch.setattr(
            native_brightness.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(0, "ok"),
        )
        assert native_brightness._run(["xrandr"]) == "ok"

        monkeypatch.setattr(
            native_brightness.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(1, "fail"),
        )
        assert native_brightness._run(["xrandr"]) is None

        def fail_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="xrandr", timeout=1)

        monkeypatch.setattr(
            native_brightness.subprocess,
            "run",
            fail_run,
        )
        assert native_brightness._run(["xrandr"]) is None

    def test_set_brightness_clamps(self, monkeypatch):
        calls: list[list[str]] = []
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: calls.append(cmd) or "",
        )
        native_brightness.set_brightness(
            Backend(output="HDMI-1"), -10.0, backlight=LinuxBacklightSource()
        )
        native_brightness.set_brightness(
            Backend(output="HDMI-1"), 10.0, backlight=LinuxBacklightSource()
        )
        assert calls[0][-1] == "0.10"
        assert calls[1][-1] == "1.00"

    def test_set_brightness_prefers_brightnessctl(self, monkeypatch):
        monkeypatch.setattr(
            native_brightness,
            "is_wayland_session",
            lambda: True,
        )
        calls: list[list[str]] = []
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: calls.append(cmd) or "",
        )
        native_brightness.set_brightness(
            Backend(output="eDP-1", brightnessctl="/usr/bin/brightnessctl"),
            0.75,
            backlight=LinuxBacklightSource(),
        )
        assert calls == [["/usr/bin/brightnessctl", "set", "75%"]]

    def test_set_brightness_prefers_xrandr_on_x11_even_with_sysfs(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            native_brightness,
            "is_wayland_session",
            lambda: False,
        )
        calls: list[list[str]] = []
        monkeypatch.setattr(
            native_brightness,
            "_run",
            lambda cmd: calls.append(cmd) or "",
        )
        (tmp_path / "max_brightness").write_text("200\n")
        (tmp_path / "brightness").write_text("100\n")

        native_brightness.set_brightness(
            Backend(output="eDP-1", sysfs=tmp_path),
            0.75,
            backlight=LinuxBacklightSource(),
        )

        assert calls == [["xrandr", "--output", "eDP-1", "--brightness", "0.75"]]
        assert (tmp_path / "brightness").read_text() == "100\n"

    def test_set_brightness_uses_sysfs_when_available(self, tmp_path):
        with patch.object(
            native_brightness,
            "is_wayland_session",
            return_value=True,
        ):
            (tmp_path / "max_brightness").write_text("200\n")
            (tmp_path / "brightness").write_text("100\n")

            native_brightness.set_brightness(
                Backend(output="eDP-1", sysfs=tmp_path),
                0.75,
                backlight=LinuxBacklightSource(),
            )

        assert (tmp_path / "brightness").read_text() == "150\n"

    def test_set_brightness_sysfs_clamps(self, tmp_path):
        with patch.object(
            native_brightness,
            "is_wayland_session",
            return_value=True,
        ):
            (tmp_path / "max_brightness").write_text("200\n")
            (tmp_path / "brightness").write_text("100\n")

            native_brightness.set_brightness(
                Backend(output="eDP-1", sysfs=tmp_path),
                -1.0,
                backlight=LinuxBacklightSource(),
            )

        assert (tmp_path / "brightness").read_text() == "20\n"

    def test_get_brightness_calls_platform_wayland_helper(self, monkeypatch, tmp_path):
        (tmp_path / "max_brightness").write_text("200\n")
        (tmp_path / "brightness").write_text("120\n")
        probe = MagicMock(return_value=True)
        monkeypatch.setattr(
            native_brightness,
            "is_wayland_session",
            probe,
        )

        assert (
            get_brightness(
                backend=Backend(output="eDP-1", sysfs=tmp_path),
                backlight=LinuxBacklightSource(),
            )
            == 0.6
        )
        probe.assert_called_once_with()


def test_linux_backend_revalidates_primary_and_stops_writes(monkeypatch, tmp_path):
    (tmp_path / "brightness").write_text("50\n")
    (tmp_path / "max_brightness").write_text("100\n")
    selected = [Backend("eDP-1", sysfs=tmp_path)]
    monkeypatch.setattr(brightness_state, "is_wayland_session", lambda: True)
    monkeypatch.setattr(
        brightness_state, "detect_output", lambda backlight: selected[0]
    )
    backend = brightness_state.UnixBrightnessBackend(LinuxBacklightSource())
    state = backend.get_state()
    assert state.level == 0.5
    assert backend.set_brightness(value=0.6, target=state.target).level == 0.6
    selected[0] = Backend("DP-2", sysfs=tmp_path)
    assert backend.set_brightness(value=1, target=state.target).target != state.target
    assert (tmp_path / "brightness").read_text() == "60\n"
    backend.stop()
    assert backend.set_brightness(value=1, target=state.target) == BrightnessState()
    assert (tmp_path / "brightness").read_text() == "60\n"


class FakeBrightnessBackend(BrightnessBackend):
    def __init__(self, level=0.5):
        self.state = (
            BrightnessState(level, "test-panel")
            if level is not None
            else BrightnessState()
        )
        self.writes = []
        self.stopped = False

    def get_state(self):
        return self.state

    def set_brightness(self, *, value, target):
        self.writes.append((value, target))
        self.state = BrightnessState(value, target)
        return self.state

    def stop(self):
        self.stopped = True


def _make_applet(brightness: float | None = 0.8) -> BrightnessApplet:
    backend = FakeBrightnessBackend(brightness)
    with (
        patch("docking.applets.brightness.applet.BackgroundWorker", _ImmediateWorker),
        patch.object(TestAppletBackendFactories, "brightness", return_value=backend),
    ):
        return BrightnessApplet(
            48, config=Config(), platform_services=make_applet_platform()
        )


class TestBrightnessApplet:
    def test_creates_with_icon(self):
        assert _make_applet().item.icon is not None

    @pytest.mark.parametrize(
        "level,percent", [(0.75, 75), (0.80, 80), (0.82, 82), (0.98, 98)]
    )
    def test_tooltip_matches_rounded_percentage(self, level, percent):
        assert _make_applet(level).item.name == f"Brightness: {percent}%"

    @pytest.mark.parametrize("size", [32, 48, 64])
    def test_icon_renders_at_various_sizes(self, size):
        pixbuf = _make_applet().create_icon(size=size)
        assert pixbuf is not None and pixbuf.get_width() == size

    @pytest.mark.parametrize(
        "level,up,expected",
        [(0.5, True, 0.52), (0.5, False, 0.48), (0.1, False, 0.1), (1.0, True, 1.0)],
    )
    def test_scroll_clamps_and_targets_selected_display(self, level, up, expected):
        applet = _make_applet(level)
        applet.on_scroll(direction_up=up)
        value, target = applet._backend.writes[-1]
        assert value == pytest.approx(expected)
        assert target == "test-panel"
        assert applet._brightness == pytest.approx(expected)

    def test_click_resets_to_full(self):
        applet = _make_applet(0.5)
        applet.on_clicked()
        assert applet._backend.writes == [(1.0, "test-panel")]
        assert applet.item.name == "Brightness: 100%"

    def test_unavailable_has_no_fictitious_level_or_requests(self):
        applet = _make_applet(None)
        applet.on_clicked()
        applet.on_scroll(direction_up=True)
        assert applet._backend.writes == []
        assert applet._brightness is None
        assert applet.item.name == "Brightness: Unavailable"
        applet._show_level = True
        assert applet.create_icon(48) is not None

    def test_tick_detects_display_arrival_and_removal(self):
        applet = _make_applet(None)
        applet._backend.state = BrightnessState(0.7, "new-panel")
        applet._tick()
        assert applet.item.name == "Brightness: 70%"
        applet._backend.state = BrightnessState()
        applet._tick()
        assert applet.item.name == "Brightness: Unavailable"

    def test_hardware_rounding_replaces_optimistic_value(self, monkeypatch):
        applet = _make_applet(0.5)
        monkeypatch.setattr(
            applet._backend,
            "set_brightness",
            lambda **kwargs: BrightnessState(0.6, "test-panel"),
        )
        applet.on_scroll(direction_up=True)
        assert applet.item.name == "Brightness: 60%"

    def test_start_stop_releases_backend_and_preserves_level_preference(
        self, monkeypatch
    ):
        applet = _make_applet(0.5)
        removed = []
        monkeypatch.setattr(
            "docking.applets.brightness.applet.GLib.timeout_add_seconds",
            lambda *args: 11,
        )
        monkeypatch.setattr(
            "docking.applets.brightness.applet.GLib.source_remove", removed.append
        )
        applet.start(notify=lambda: None)
        assert applet._timer_id == 11
        items = applet.get_menu_items()
        show = next(item for item in items if item.get_label() == "Show Level")
        show.set_active(True)
        applet._on_toggle_level(show)
        assert applet._show_level is True
        assert applet._config.applet_prefs["brightness"]["show_level"] is True
        applet.stop()
        applet.on_clicked()
        applet.on_scroll(direction_up=True)
        assert applet._backend.stopped
        assert applet._backend.writes == []
        assert applet._timer_id == 0
        assert removed == [11]
        assert applet._tick() is False

    def test_unchanged_poll_does_not_redraw(self):
        applet = _make_applet(0.5)
        applet.present = MagicMock()
        applet._tick()
        applet.present.assert_not_called()

    def test_backend_selection_keeps_platform_imports_out_of_state(self):
        from docking.applets.brightness.implementations.unix.backend import (
            UnixBrightnessBackend,
        )
        from docking.applets.brightness.implementations.windows.backend import (
            WindowsBrightnessBackend,
        )
        from docking.platform.composition.linux import LinuxAppletBackendFactories
        from docking.platform.composition.windows import WindowsAppletBackendFactories

        assert isinstance(
            WindowsAppletBackendFactories().brightness(), WindowsBrightnessBackend
        )
        assert isinstance(
            LinuxAppletBackendFactories().brightness(), UnixBrightnessBackend
        )


def test_unix_poll_preserves_read_failure_and_small_change_behavior(monkeypatch):
    levels = iter((0.5, None, 0.505, 0.8))
    panel = Backend("eDP-1")
    monkeypatch.setattr(brightness_state, "detect_output", lambda _source: panel)
    monkeypatch.setattr(brightness_state, "get_brightness", lambda *_args: next(levels))
    backend = brightness_state.UnixBrightnessBackend(LinuxBacklightSource())
    first = backend.get_state()
    assert first.level == 0.5
    assert backend.get_state() == first
    assert backend.get_state() == first
    assert backend.get_state().level == 0.8
    panel = None
    assert backend.get_state() == BrightnessState()


def test_brightness_restart_creates_a_live_backend_and_rejects_old_poll(monkeypatch):
    applet = _make_applet(0.5)
    first = applet._backend
    generation = applet._poll_generation
    applet.stop()
    second = FakeBrightnessBackend(0.8)
    monkeypatch.setattr(TestAppletBackendFactories, "brightness", lambda self: second)
    applet.start(notify=lambda: None)
    try:
        assert first.stopped
        assert applet._backend is second
        assert applet.item.name == "Brightness: 80%"
        applet._on_poll_result(
            BrightnessState(0.1, "test-panel"), generation=generation
        )
        assert applet.item.name == "Brightness: 80%"
        applet.on_scroll(direction_up=False)
        assert second.writes[-1][0] == pytest.approx(0.78)
    finally:
        applet.stop()
