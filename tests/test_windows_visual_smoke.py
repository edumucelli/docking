"""Native desktop smoke must distinguish the dock from GTK's transient popups."""

import pytest

from tools.windows.visual.smoke import dock_window


def test_startup_hover_popup_cannot_replace_the_dock_observation():
    tooltip = {"hwnd": 10, "title": "Docking", "class": "gdkWindowTemp"}
    dock = {"hwnd": 20, "title": "Docking", "class": "gdkWindowToplevel"}
    assert dock_window([tooltip, dock]) is dock
    # Moving the pointer away hides the tooltip while the dock stays visible.
    assert dock_window([dock]) is dock


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"hwnd": 10, "title": "Docking", "class": "gdkWindowTemp"}],
        [{"hwnd": 20, "title": "Preferences", "class": "gdkWindowToplevel"}],
    ],
)
def test_transient_or_unrelated_windows_do_not_establish_dock_startup(rows):
    assert dock_window(rows) is None
