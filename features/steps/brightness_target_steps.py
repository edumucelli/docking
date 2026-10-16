"""Verify real brightness presentation using temporary hardware and xrandr fixtures."""

from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from behave import given, then

import docking.applets.brightness.state as state_mod
from docking.applets.brightness.applet import BrightnessApplet
from docking.applets.brightness.render import create_icon
from docking.core.config import Config


@given('brightness controls for a "{setup}" display setup')
def brightness_setup(context, setup):
    temporary = TemporaryDirectory(prefix="docking-bdd-backlight-")
    context.add_cleanup(temporary.cleanup)
    folder = Path(temporary.name)
    patches = ExitStack()
    context.add_cleanup(patches.close)
    patches.enter_context(patch.object(state_mod, "_BACKLIGHT_DIR", folder))
    patches.enter_context(patch.object(state_mod.shutil, "which", return_value=None))
    patches.enter_context(
        patch.object(
            state_mod, "is_wayland_session", return_value=setup == "native Wayland"
        )
    )
    if setup == "native Wayland":
        panel = folder / "panel"
        panel.mkdir()
        (panel / "brightness").write_text("60\n")
        (panel / "max_brightness").write_text("100\n")
        patches.enter_context(patch.object(state_mod, "_run", return_value=None))
    else:
        assert setup in {"secondary primary", "similar output names"}
        monitors = (
            "Monitors: 2\n"
            " 0: +HDMI-1 1920/480x1080/270+0+0 HDMI-1\n"
            " 1: +*DP-1 1920/480x1080/270+1920+0 DP-1\n"
            if setup == "secondary primary"
            else "Monitors: 2\n 0: +*DP-1 1920/480x1080/270+0+0 DP-1\n"
        )
        first = "HDMI-1" if setup == "secondary primary" else "eDP-1"
        verbose = (
            f"{first} connected\n\tBrightness: 0.25\n"
            "DP-1 connected\n\tBrightness: 0.75\n"
        )
        patches.enter_context(
            patch.object(
                state_mod,
                "_run",
                side_effect=lambda cmd: (
                    monitors if "--listmonitors" in cmd else verbose
                ),
            )
        )
    config = Config(applet_prefs={"brightness": {"show_level": True}})
    context.brightness_applet = BrightnessApplet(48, config=config)


@then("the brightness tooltip and icon show {percent:d} percent")
def brightness_presentation(context, percent):
    applet = context.brightness_applet
    assert applet.item.name == f"Brightness: {percent}%"
    expected = create_icon(size=48, brightness=percent / 100, show_level=True)
    assert bytes(applet.item.icon.get_pixels()) == bytes(expected.get_pixels())
