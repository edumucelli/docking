"""Selected display hardware determines the applet's tooltip and pixels."""

import pytest

import docking.applets.brightness.implementations.linux.backlight as native_backlight
import docking.applets.brightness.implementations.unix.backend as state_mod
from docking.applets.brightness.applet import BrightnessApplet
from docking.applets.brightness.implementations.linux.backlight import (
    LinuxBacklightSource,
)
from docking.applets.brightness.render import create_icon
from docking.core.config import Config
from tests.applets.test_brightness import _ImmediateWorker
from tests.platform_factories import TestAppletBackendFactories, make_applet_platform


@pytest.mark.parametrize(
    "setup,percent", [("wayland", 60), ("primary", 75), ("similar", 75)]
)
def test_selected_display_brightness_is_presented(
    monkeypatch, tmp_path, setup, percent
):
    monkeypatch.setattr(
        "docking.applets.brightness.applet.BackgroundWorker", _ImmediateWorker
    )
    monkeypatch.setattr(
        TestAppletBackendFactories,
        "brightness",
        staticmethod(lambda: state_mod.UnixBrightnessBackend(LinuxBacklightSource())),
    )
    monkeypatch.setattr(native_backlight, "_BACKLIGHT_DIR", tmp_path)
    monkeypatch.setattr(state_mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(state_mod, "is_wayland_session", lambda: setup == "wayland")
    if setup == "wayland":
        panel = tmp_path / "panel"
        panel.mkdir()
        (panel / "brightness").write_text("60\n")
        (panel / "max_brightness").write_text("100\n")
        monkeypatch.setattr(
            state_mod,
            "_run",
            lambda cmd: None,
        )
    else:
        monitors = (
            "Monitors: 2\n 0: +HDMI-1 1920/480x1080/270+0+0 HDMI-1\n"
            " 1: +*DP-1 1920/480x1080/270+1920+0 DP-1\n"
            if setup == "primary"
            else "Monitors: 2\n 0: +*DP-1 1920/480x1080/270+0+0 DP-1\n"
        )
        first = "HDMI-1" if setup == "primary" else "eDP-1"
        verbose = f"{first} connected\n\tBrightness: 0.25\nDP-1 connected\n\tBrightness: 0.75\n"
        monkeypatch.setattr(
            state_mod,
            "_run",
            lambda cmd: monitors if "--listmonitors" in cmd else verbose,
        )
    applet = BrightnessApplet(
        48,
        config=Config(applet_prefs={"brightness": {"show_level": True}}),
        platform_services=make_applet_platform(),
    )
    try:
        applet._tick()
        assert applet.item.name == f"Brightness: {percent}%"
        expected = create_icon(size=48, brightness=percent / 100, show_level=True)
        assert bytes(applet.item.icon.get_pixels()) == bytes(expected.get_pixels())
    finally:
        applet.stop()
