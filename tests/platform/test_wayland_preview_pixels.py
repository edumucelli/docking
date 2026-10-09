"""Real shared-memory preview conversion, scaling and buffer ownership."""

import mmap
import random

import pytest
from gi.repository import GdkPixbuf, GLib

from docking.platform.backends.base import DisplayServer, WindowId
from docking.platform.backends.wayland.previews import (
    SHM_ARGB8888,
    SHM_XRGB8888,
    _CaptureRequest,
    _HyprlandCaptureRequest,
    _PhocCaptureRequest,
    _pixbuf_from_request,
)


def _request(kind, **kwargs):
    common = dict(window_id=WindowId(DisplayServer.WAYLAND, 1), **kwargs)
    if kind == "wayland":
        return _CaptureRequest(source=None, session=None, **common)
    if kind == "hyprland":
        return _HyprlandCaptureRequest(frame=None, **common)
    return _PhocCaptureRequest(frame=None, **common)


def _pixels(pixbuf):
    raw = pixbuf.get_pixels()
    width, height, stride = (
        pixbuf.get_width(),
        pixbuf.get_height(),
        pixbuf.get_rowstride(),
    )
    return b"".join(
        raw[row * stride : row * stride + width * 4] for row in range(height)
    )


@pytest.mark.parametrize("kind", ["wayland", "hyprland", "phoc"])
@pytest.mark.parametrize("format_", [SHM_ARGB8888, SHM_XRGB8888])
@pytest.mark.parametrize("inverted", [False, True])
@pytest.mark.parametrize("padding", [0, 1, 2, 3, 8])
def test_channels_alpha_stride_and_inversion(kind, format_, inverted, padding):
    bgra = [
        bytes([3, 2, 1, 0, 13, 12, 11, 64, 23, 22, 21, 128]),
        bytes([33, 32, 31, 192, 43, 42, 41, 254, 53, 52, 51, 255]),
    ]
    source = b"".join(row + b"\x91" * padding for row in bgra)
    rows = list(reversed(bgra)) if inverted else bgra
    expected = b"".join(
        bytes(
            (
                row[index + 2],
                row[index + 1],
                row[index],
                row[index + 3] if format_ == SHM_ARGB8888 else 255,
            )
        )
        for row in rows
        for index in range(0, len(row), 4)
    )
    with mmap.mmap(-1, len(source)) as buffer:
        buffer[:] = source
        request = _request(
            kind,
            requested_width=3,
            requested_height=2,
            width=3,
            height=2,
            stride=12 + padding,
            format=format_,
            y_inverted=inverted,
            mmap_obj=buffer,
        )

        preview = _pixbuf_from_request(request)

        assert buffer[:] == source
    # Returned pixel storage remains valid after the capture mmap is closed.
    assert (preview.width, preview.height) == (3, 2)
    assert _pixels(preview.image) == expected


def test_scaled_pixels_match_scalar_conversion_for_random_buffers():
    rng = random.Random(86927044)
    for _ in range(200):
        width, height = rng.randrange(1, 40), rng.randrange(1, 40)
        stride = width * 4 + rng.randrange(4) * 4
        format_ = rng.choice([SHM_ARGB8888, SHM_XRGB8888])
        inverted = rng.choice([False, True])
        target_width, target_height = rng.randrange(1, 50), rng.randrange(1, 50)
        source = rng.randbytes(stride * height)
        rows = [
            source[index : index + stride] for index in range(0, len(source), stride)
        ]
        ordered = b"".join(reversed(rows)) if inverted else source
        rgba = bytearray(len(ordered))
        for index in range(0, len(ordered), 4):
            rgba[index : index + 4] = bytes(
                (
                    ordered[index + 2],
                    ordered[index + 1],
                    ordered[index],
                    ordered[index + 3] if format_ == SHM_ARGB8888 else 255,
                )
            )
        reference = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(bytes(rgba)),
            GdkPixbuf.Colorspace.RGB,
            True,
            8,
            width,
            height,
            stride,
        ).scale_simple(target_width, target_height, GdkPixbuf.InterpType.BILINEAR)
        with mmap.mmap(-1, len(source)) as buffer:
            buffer[:] = source
            request = _request(
                "wayland",
                requested_width=target_width,
                requested_height=target_height,
                width=width,
                height=height,
                stride=stride,
                format=format_,
                y_inverted=inverted,
                mmap_obj=buffer,
            )
            preview = _pixbuf_from_request(request)
        assert (preview.width, preview.height) == (target_width, target_height)
        assert _pixels(preview.image) == _pixels(reference)
