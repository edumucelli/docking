"""Copy borrowed notification HICONs into immutable straight-alpha pixels."""

from __future__ import annotations

import ctypes as c

from docking.platform.backends.windows.ffi import bind
from docking.platform.tray.state import TrayIconPixmap


class _Header(c.Structure):
    _fields_ = [
        ("size", c.c_uint32),
        ("width", c.c_int32),
        ("height", c.c_int32),
        ("planes", c.c_uint16),
        ("bits", c.c_uint16),
        ("compression", c.c_uint32),
        ("bytes", c.c_uint32),
        ("x", c.c_int32),
        ("y", c.c_int32),
        ("colors", c.c_uint32),
        ("important", c.c_uint32),
    ]


def composite_pixels(black: bytes, white: bytes) -> bytes:
    """Recover alpha from two GDI composites, including classic masked icons."""
    if len(black) != len(white) or len(black) % 4:
        raise ValueError("Invalid icon composites")
    rgba = bytearray(len(black))
    for offset in range(0, len(black), 4):
        dark = black[offset : offset + 3]
        light = white[offset : offset + 3]
        alpha = max(
            0, min(255, 255 - max(b - a for a, b in zip(dark, light, strict=True)))
        )
        rgb = tuple(min(255, v * 255 // alpha) if alpha else 0 for v in reversed(dark))
        rgba[offset : offset + 4] = bytes((*rgb, alpha))
    return bytes(rgba)


def icon_pixels(handle: int, size: int = 32) -> TrayIconPixmap | None:
    if not handle or not 1 <= size <= 256:
        return None
    p, u32, boolean = c.c_void_p, c.c_uint32, c.c_int32
    gdi = c.WinDLL("gdi32", use_last_error=True)
    user = c.WinDLL("user32", use_last_error=True)
    dc = bind(gdi, "CreateCompatibleDC", p, p)(None)
    if not dc:
        return None
    bitmap, previous = None, None
    try:
        header = _Header(
            size=c.sizeof(_Header), width=size, height=-size, planes=1, bits=32
        )
        pixels = p()
        bitmap = bind(gdi, "CreateDIBSection", p, p, p, u32, c.POINTER(p), p, u32)(
            dc, c.byref(header), 0, c.byref(pixels), None, 0
        )
        if not bitmap or not pixels.value:
            return None
        previous = bind(gdi, "SelectObject", p, p, p)(dc, bitmap)
        if previous in (None, p(-1).value):
            previous = None
            return None
        draw = bind(
            user,
            "DrawIconEx",
            boolean,
            p,
            c.c_int,
            c.c_int,
            p,
            c.c_int,
            c.c_int,
            u32,
            p,
            u32,
        )
        count = size * size * 4
        c.memset(pixels, 0, count)
        if not draw(dc, 0, 0, handle, size, size, 0, None, 3):
            return None
        bind(gdi, "GdiFlush", boolean)()
        black = c.string_at(pixels, count)
        c.memset(pixels, 255, count)
        if not draw(dc, 0, 0, handle, size, size, 0, None, 3):
            return None
        bind(gdi, "GdiFlush", boolean)()
        return TrayIconPixmap(
            size, size, composite_pixels(black, c.string_at(pixels, count))
        )
    finally:
        if previous:
            bind(gdi, "SelectObject", p, p, p)(dc, previous)
        if bitmap:
            bind(gdi, "DeleteObject", boolean, p)(bitmap)
        bind(gdi, "DeleteDC", boolean, p)(dc)
