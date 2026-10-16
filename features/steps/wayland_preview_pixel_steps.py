"""Actual mmap and GdkPixbuf ownership contracts for the Wayland decoder."""

import mmap

from behave import given, then, when

from docking.platform.backends.base import DisplayServer, WindowId
from docking.platform.backends.wayland.previews import (
    SHM_ARGB8888,
    SHM_XRGB8888,
    _HyprlandCaptureRequest,
    _pixbuf_from_request,
)
from tests.platform.test_wayland_preview_pixels import _request


@given('a wide "{kind}" Wayland capture requested within a square')
def wide_capture(context, kind):
    source = bytes((0, 0, 255, 255)) * (8 * 4)
    buffer = mmap.mmap(-1, len(source))
    buffer[:] = source
    context.add_cleanup(buffer.close)
    context.wide_request = _request(
        kind,
        requested_width=6,
        requested_height=6,
        width=8,
        height=4,
        stride=32,
        format=SHM_ARGB8888,
        mmap_obj=buffer,
    )


@when("the wide Wayland capture is decoded")
def decode_wide_capture(context):
    context.wide_preview = _pixbuf_from_request(context.wide_request)


@then("the preview keeps the wide window's aspect ratio")
def wide_preview_shape(context):
    assert (context.wide_preview.width, context.wide_preview.height) == (6, 3)
    assert context.wide_preview.image.get_pixels()[:4] == b"\xff\x00\x00\xff"


@given('a padded "{format_}" Wayland capture with "{orientation}" rows')
def capture_pixels(context, format_, orientation):
    rows = [bytes((3, 2, 1, 64)), bytes((33, 32, 31, 128))]
    source = b"".join(row + b"\x91" * 4 for row in rows)
    buffer = mmap.mmap(-1, len(source))
    context.add_cleanup(buffer.close)
    buffer[:] = source
    context.pixel_source = source
    context.pixel_request = _HyprlandCaptureRequest(
        window_id=WindowId(DisplayServer.WAYLAND, 1),
        requested_width=1,
        requested_height=2,
        frame=None,
        width=1,
        height=2,
        stride=8,
        format=SHM_ARGB8888 if format_ == "ARGB" else SHM_XRGB8888,
        y_inverted=orientation == "inverted",
        mmap_obj=buffer,
    )


@when("the Wayland preview pixels are decoded and capture storage is released")
def decode_capture_pixels(context):
    context.pixel_preview = _pixbuf_from_request(context.pixel_request)
    assert context.pixel_request.mmap_obj[:] == context.pixel_source
    context.pixel_request.mmap_obj.close()


@then("preview colors and alpha match the original capture")
def decoded_capture_matches(context):
    request, preview = context.pixel_request, context.pixel_preview
    assert (preview.width, preview.height) == (1, 2)
    rows = [(1, 2, 3, 64), (31, 32, 33, 128)]
    if request.y_inverted:
        rows.reverse()
    raw, stride = preview.image.get_pixels(), preview.image.get_rowstride()
    for index, (r, g, b, a) in enumerate(rows):
        alpha = a if request.format == SHM_ARGB8888 else 255
        assert raw[index * stride : index * stride + 4] == bytes((r, g, b, alpha))
