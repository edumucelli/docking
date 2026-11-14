# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Pure state helpers for Clippy applet."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from docking.i18n import _

MAX_DISPLAY_LEN = 50
DEFAULT_MAX_ENTRIES = 15
MIN_MAX_ENTRIES = 1
MAX_MAX_ENTRIES = 500
MAX_IMAGE_BYTES = 32 * 1024 * 1024
_HEX_COLOR = re.compile(
    r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})"
)
ClipKey = str | tuple[bytes, int, int, bool]


@dataclass(frozen=True)
class Clip:
    text: str = ""
    pixels: bytes = field(default=b"", repr=False, compare=False)
    width: int = 0
    height: int = 0
    alpha: bool = False
    image_key: bytes = b""

    @classmethod
    def from_image(
        cls, *, pixels: bytes, width: int, height: int, rowstride: int, alpha: bool
    ) -> Clip:
        channels = 4 if alpha else 3
        row_bytes = width * channels
        if (
            not 0 < width <= 32768
            or not 0 < height <= 32768
            or rowstride < row_bytes
            or len(pixels) < rowstride * (height - 1) + row_bytes
            or len(pixels) > MAX_IMAGE_BYTES
        ):
            raise ValueError("Invalid clipboard image")
        # Ignore row padding so copying the same pixels does not create duplicates.
        packed = (
            pixels[: row_bytes * height]
            if rowstride == row_bytes
            else b"".join(
                pixels[y * rowstride : y * rowstride + row_bytes] for y in range(height)
            )
        )
        return cls(
            pixels=packed,
            width=width,
            height=height,
            alpha=alpha,
            image_key=hashlib.sha256(packed).digest(),
        )

    @property
    def key(self) -> ClipKey:
        return (
            (self.image_key, self.width, self.height, self.alpha)
            if self.pixels
            else self.text
        )

    @property
    def label(self) -> str:
        if self.pixels:
            return _("Image ({width} × {height})").format(
                width=self.width, height=self.height
            )
        return _truncate(self.text)


def normalize_max_entries(value: object) -> int:
    """Coerce the persisted history cap, falling back to the default.

    A raw negative cap raised IndexError on every clipboard change, so history
    stopped recording for good. Zero disables history.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return DEFAULT_MAX_ENTRIES
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_MAX_ENTRIES
    if parsed == 0:
        return 0
    return max(MIN_MAX_ENTRIES, min(MAX_MAX_ENTRIES, parsed))


def _truncate(text: str, max_len: int = MAX_DISPLAY_LEN) -> str:
    """Truncate text for menu display, replacing newlines with spaces."""
    clean = text.replace("\n", " ").replace("\t", " ").strip()
    if len(clean) > max_len:
        return clean[:max_len] + "..."
    return clean


def tooltip_text(clips: list[Clip], cur_position: int) -> str:
    """Build tooltip from clip history and current cursor position."""
    if clips and 0 < cur_position <= len(clips):
        return clips[cur_position - 1].label
    return _("Clippy (empty)")


def add_clip(clips: list[Clip], clip: Clip, max_entries: int) -> tuple[list[Clip], int]:
    """Add clip to history (dedup, cap) and return updated list/position."""
    next_clips = trim_clips(
        [existing for existing in clips if existing.key != clip.key] + [clip],
        max_entries=max_entries,
    )
    return next_clips, len(next_clips)


def trim_clips(clips: list[Clip], max_entries: int) -> list[Clip]:
    retained = list(clips)
    image_bytes = sum(len(clip.pixels) for clip in retained)
    while retained and (len(retained) > max_entries or image_bytes > MAX_IMAGE_BYTES):
        image_bytes -= len(retained.pop(0).pixels)
    return retained


def parse_hex_color(text: str) -> tuple[float, float, float, float] | None:
    value = text.strip()
    if _HEX_COLOR.fullmatch(value) is None:
        return None
    digits = value[1:]
    if len(digits) in (3, 4):
        digits = "".join(digit * 2 for digit in digits)
    if len(digits) == 6:
        digits += "ff"
    return (
        int(digits[0:2], 16) / 255,
        int(digits[2:4], 16) / 255,
        int(digits[4:6], 16) / 255,
        int(digits[6:8], 16) / 255,
    )


def cycle_position(clips_len: int, cur_position: int, direction_up: bool) -> int:
    """Cycle cursor position through history with wraparound."""
    if clips_len <= 0:
        return 0
    if direction_up:
        next_pos = cur_position - 1
        if next_pos < 1:
            next_pos = clips_len
        return next_pos

    next_pos = cur_position + 1
    if next_pos > clips_len:
        next_pos = 1
    return next_pos
