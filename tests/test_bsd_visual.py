"""Reject blank, misplaced and incomplete screenshots in the BSD visual lab."""

import pytest

from tools.bsd_visual.pixels import Frame, placement
from tools.bsd_visual.run import report


def frame(width=200, height=160, rect=None, channels=3, padding=0):
    stride = width * channels + padding
    pixels = bytearray(stride * height)
    if rect:
        left, top, right, bottom = rect
        for y in range(top, bottom):
            for x in range(left, right):
                start = y * stride + x * channels
                pixels[start : start + 3] = bytes((30, 190, 220))
    return Frame(width, height, channels, stride, bytes(pixels))


@pytest.mark.parametrize("channels,padding", [(3, 0), (3, 2), (4, 4)])
def test_markers_respect_channels_and_row_padding(channels, padding):
    image = frame(rect=(80, 120, 120, 150), channels=channels, padding=padding)
    assert placement(image, "bottom")["count"] == 1200
    assert placement(image, "bottom")["distance"] == 10


@pytest.mark.parametrize(
    "rect", [None, (80, 120, 90, 130), (0, 0, 40, 30), (80, 20, 120, 50)]
)
def test_blank_incomplete_off_center_or_wrong_edge_fails(rect):
    with pytest.raises(AssertionError):
        placement(frame(rect=rect), "bottom")


def test_tooltip_comparison_excludes_changes_within_the_dock():
    empty = frame()
    dock = frame(rect=(80, 120, 120, 150))
    assert empty.changed_above(dock, 100) == 0
    assert empty.changed_above(dock, 160) == 1200


def test_report_retains_failures_and_escapes_error_text(tmp_path):
    report(
        tmp_path,
        {"os": "test"},
        [{"case": "startup", "status": "fail", "detail": "<failed>"}],
    )
    html = (tmp_path / "index.html").read_text()
    assert "&lt;failed&gt;" in html
    assert '"status": "fail"' in (tmp_path / "results.json").read_text()
