"""Bounded decoding of the shell's fixed-width notification-area wire format.

Shell_NotifyIcon marshals both 32-bit and 64-bit clients into this Unicode
32-bit-handle layout. It is not a native ctypes NOTIFYICONDATA structure.
Only explicitly flagged fields may update an existing icon.
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass

from docking.platform.backends.base import Rect


@dataclass(frozen=True)
class NotifyMessage:
    operation: int
    window: int
    uid: int
    flags: int
    callback: int
    icon: int
    tip: str
    state: int
    state_mask: int
    version: int
    guid: str

    @property
    def identifier(self) -> str:
        return (
            f"guid:{self.guid}" if self.guid else f"window:{self.window:08x}:{self.uid}"
        )


def _text(data: bytes) -> str:
    return data.decode("utf-16-le", errors="replace").split("\0", 1)[0]


def decode_message(payload: bytes) -> NotifyMessage | None:
    if not 160 <= len(payload) <= 8192:
        return None
    signature, operation, size = struct.unpack_from("<III", payload)
    if signature != 0x34753423 or operation not in (0, 1, 2, 3, 4):
        return None
    if size not in (152, 936, 952, 956) or len(payload) < 8 + size:
        return None
    data = payload[8 : 8 + size]
    _size, window, uid, flags, callback, icon = struct.unpack_from("<6I", data)
    tip = _text(data[24 : 152 if size == 152 else 280])
    state, mask = struct.unpack_from("<II", data, 280) if size >= 936 else (0, 0)
    version = struct.unpack_from("<I", data, 800)[0] if size >= 936 else 0
    guid = ""
    if flags & 0x20 and size >= 952:
        parsed = uuid.UUID(bytes_le=data[936:952])
        if parsed.int:
            guid = str(parsed)
    return NotifyMessage(
        operation, window, uid, flags, callback, icon, tip, state, mask, version, guid
    )


def callback_parameters(
    *, version: int, uid: int, event: int, x: int, y: int
) -> tuple[int, int]:
    if version < 4:
        return uid, event
    point = (x & 0xFFFF) | ((y & 0xFFFF) << 16)
    return point, (event & 0xFFFF) | ((uid & 0xFFFF) << 16)


@dataclass(frozen=True)
class IconQuery:
    operation: int
    identifier: str


def decode_icon_query(payload: bytes) -> IconQuery | None:
    if len(payload) != 40:
        return None
    signature, operation, size, _padding, window, uid = struct.unpack_from(
        "<6I", payload
    )
    if signature != 0x34753423 or operation not in (1, 2) or size != 32:
        return None
    guid = uuid.UUID(bytes_le=payload[24:40])
    identifier = f"guid:{guid}" if guid.int else f"window:{window:08x}:{uid}"
    return IconQuery(operation, identifier)


def icon_query_reply(query: IconQuery, rect: Rect) -> int | None:
    # Native traces prove operation 2 returns width/height, not a second corner.
    x, y = (rect.x, rect.y) if query.operation == 1 else (rect.width, rect.height)
    if (
        rect.width <= 0
        or rect.height <= 0
        or not (-32768 <= x <= 32767 and -32768 <= y <= 32767)
    ):
        return None
    return (x & 0xFFFF) | ((y & 0xFFFF) << 16)
