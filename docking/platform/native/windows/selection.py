"""Complete Shell selections prepared from native namespace item identities."""

from __future__ import annotations

import ctypes
import struct
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import PureWindowsPath

from docking.platform.native.windows.com import HRESULT, apartment
from docking.platform.native.windows.dataobject import file_drop_bytes
from docking.platform.native.windows.ffi import bind

_MAX_SELECTION_BYTES = 1048576


def _shell_id_list(identifiers: Sequence[bytes]) -> bytes:
    # CIDA permits Desktop as an empty parent and desktop-relative full PIDLs.
    # Unlike SHCreateDataObject's child-array argument, these can contain
    # multiple SHITEMIDs and therefore represent different parent folders.
    if not identifiers:
        raise ValueError("A Shell selection must not be empty")
    offset = 4 * (len(identifiers) + 2)
    offsets = [offset]
    chunks = [b"\0\0"]  # Empty Desktop parent.
    offset += 2
    for identifier in identifiers:
        position = 0
        while position + 2 <= len(identifier):
            size = struct.unpack_from("<H", identifier, position)[0]
            if size == 0:
                break
            if size < 2:
                raise ValueError("Invalid Shell item identifier")
            position += size
        else:
            raise ValueError("Unterminated Shell item identifier")
        if position != len(identifier) - 2 or position == 0:
            raise ValueError("Incomplete Shell item identifier")
        offsets.append(offset)
        chunks.append(identifier)
        offset += len(identifier)
        if offset > _MAX_SELECTION_BYTES:
            raise OSError("Shell selection exceeds the byte limit")
    return struct.pack(
        "<" + "I" * (len(offsets) + 1), len(identifiers), *offsets
    ) + b"".join(chunks)


def shell_selection_formats(
    targets: Sequence[str], *, files: bool
) -> tuple[tuple[str, bytes], ...]:
    """Prepare CIDA and file/URL data for the entire ordered selection."""
    if not targets or any(not target or "\0" in target for target in targets):
        raise ValueError("A Shell selection must contain nonempty targets")
    if files and any(not PureWindowsPath(target).is_absolute() for target in targets):
        raise ValueError("Shell file targets must be absolute native paths")
    if not files and len(targets) != 1:
        raise ValueError("A Shell protocol selection must contain one target")
    remaining = _MAX_SELECTION_BYTES - 4 * (len(targets) + 2) - 2
    if remaining < 2:
        raise OSError("Shell selection exceeds the byte limit")
    with apartment() as ole, ExitStack() as stack:
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        parse = bind(
            shell,
            "SHParseDisplayName",
            HRESULT,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        )
        free = bind(ole, "CoTaskMemFree", None, ctypes.c_void_p)
        size_of = bind(shell, "ILGetSize", ctypes.c_uint32, ctypes.c_void_p)
        identifiers = []
        for target in targets:
            identifier = ctypes.c_void_p()
            try:
                status = parse(target, None, ctypes.byref(identifier), 0, None)
            finally:
                if identifier.value:
                    stack.callback(free, identifier)
            if status != 0:
                raise OSError(status & 0xFFFFFFFF, "SHParseDisplayName failed")
            if not identifier.value:
                raise OSError("Missing Shell selection identity")
            length = size_of(identifier)
            if not 2 < length <= remaining:
                raise OSError("Invalid Shell selection identity length")
            identifiers.append(ctypes.string_at(identifier, length))
            remaining -= length
        formats = (("Shell IDList Array", _shell_id_list(identifiers)),)
    if files:
        return (*formats, ("CF_HDROP", file_drop_bytes(targets)))
    return (
        *formats,
        ("UniformResourceLocatorW", (targets[0] + "\0").encode("utf-16-le")),
    )
