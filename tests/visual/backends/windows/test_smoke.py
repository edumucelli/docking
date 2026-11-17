"""Native desktop smoke must distinguish the dock from GTK's transient popups."""

import pytest

from tests.visual.backends.windows.smoke import dock_window, placement_pixels


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


@pytest.mark.parametrize("edge", ["bottom", "top", "left", "right"])
@pytest.mark.parametrize("scale", [1, 2])
def test_native_placement_uses_expected_workarea_and_dpi(edge, scale):
    from PIL import Image, ImageDraw

    before = Image.new("RGB", (800 * scale, 600 * scale))
    after = before.copy()
    boxes = {
        "bottom": (300, 555, 499, 594),
        "top": (300, 5, 499, 44),
        "left": (5, 200, 44, 399),
        "right": (755, 200, 794, 399),
    }
    ImageDraw.Draw(after).rectangle(
        tuple(value * scale for value in boxes[edge]), fill="magenta"
    )
    result = placement_pixels(
        before, after, (0, 0, 800 * scale, 600 * scale), edge, 96 * scale
    )
    assert result["pixels"] >= 1000 * scale * scale


@pytest.mark.parametrize("box", [None, (300, 5, 499, 44), (0, 555, 199, 594)])
def test_native_placement_rejects_blank_wrong_edge_and_off_center_pixels(box):
    from PIL import Image, ImageDraw

    before = Image.new("RGB", (800, 600))
    after = before.copy()
    if box:
        ImageDraw.Draw(after).rectangle(box, fill="magenta")
    with pytest.raises(AssertionError):
        placement_pixels(before, after, (0, 0, 800, 600), "bottom", 96)


def test_native_placement_translates_negative_monitor_origins():
    from PIL import Image, ImageDraw

    before = Image.new("RGB", (800, 600))
    after = before.copy()
    ImageDraw.Draw(after).rectangle((300, 555, 499, 594), fill="magenta")
    assert (
        placement_pixels(before, after, (-800, 0, 0, 600), "bottom", 96, (-800, 0))[
            "pixels"
        ]
        == 8000
    )


@pytest.mark.parametrize("gap_applied", [False, True])
def test_native_gap_coverage_rejects_an_unchanged_edge_position(gap_applied):
    from PIL import Image, ImageDraw

    before = Image.new("RGB", (800, 600))
    after = before.copy()
    offset = 40 if gap_applied else 0
    ImageDraw.Draw(after).rectangle(
        (300, 555 - offset, 499, 594 - offset), fill="magenta"
    )
    if gap_applied:
        assert placement_pixels(before, after, (0, 0, 800, 600), "bottom", 96, gap=40)
    else:
        with pytest.raises(AssertionError, match="gap absent"):
            placement_pixels(before, after, (0, 0, 800, 600), "bottom", 96, gap=40)


def test_background_resize_cannot_fill_the_measured_dock_gap():
    from PIL import Image, ImageDraw

    before = Image.new("RGB", (800, 600))
    after = before.copy()
    draw = ImageDraw.Draw(after)
    draw.rectangle((0, 440, 799, 599), fill="white")
    draw.rectangle((300, 515, 499, 554), fill="magenta")
    result = placement_pixels(before, after, (0, 0, 800, 600), "bottom", 96, gap=40)
    assert result["pixels"] == 8000
    assert result["distance"] == 45
