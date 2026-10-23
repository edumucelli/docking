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
