"""Unicode Shell links, read without resolving or launching their targets."""

from __future__ import annotations

import ctypes
import os
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .com import GUID, HRESULT, apartment, method, query, release
from .ffi import bind


@dataclass(frozen=True, slots=True)
class ShellLink:
    executable: Path
    working_directory: str
    arguments: str


def _check(status: int) -> None:
    if status < 0:
        raise OSError(status & 0xFFFFFFFF, "Windows Shell link operation failed")


@contextmanager
def _link() -> Iterator[tuple[ctypes.c_void_p, ctypes.c_void_p]]:
    with apartment() as ole, ExitStack() as stack:
        clsid = GUID.parse("00021401-0000-0000-c000-000000000046")
        iid = GUID.parse("000214f9-0000-0000-c000-000000000046")
        pointer = ctypes.c_void_p()
        create = bind(
            ole,
            "CoCreateInstance",
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        status = create(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer)
        )
        stack.callback(release, pointer)
        _check(status)
        persistence = query(
            stack, pointer, GUID.parse("0000010b-0000-0000-c000-000000000046")
        )
        yield pointer, persistence


def read_shortcut(path: Path) -> ShellLink:
    with _link() as (pointer, persistence):
        _check(
            method(persistence, 5, HRESULT, ctypes.c_wchar_p, ctypes.c_uint32)(
                persistence, str(path.resolve()), 0
            )
        )
        target = ctypes.create_unicode_buffer(32768)
        # SLGP_RAWPATH returns stored metadata. Never call Resolve, which can
        # search for moved targets or show UI during application discovery.
        _check(
            method(
                pointer,
                3,
                HRESULT,
                ctypes.c_wchar_p,
                ctypes.c_int32,
                ctypes.c_void_p,
                ctypes.c_uint32,
            )(pointer, target, len(target), None, 4)
        )
        if not target.value:
            raise OSError("Shell link has no executable target")
        values = []
        for index in (8, 10):  # GetWorkingDirectory, GetArguments
            buffer = ctypes.create_unicode_buffer(32768)
            _check(
                method(pointer, index, HRESULT, ctypes.c_wchar_p, ctypes.c_int32)(
                    pointer, buffer, len(buffer)
                )
            )
            values.append(buffer.value)
        return ShellLink(Path(target.value), values[0], values[1])


def write_shortcut(path: Path, *, executable: Path, name: str) -> None:
    """Atomically persist a direct executable launcher with its own directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".lnk", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(filename)
    try:
        with _link() as (pointer, persistence):
            for index, text in (
                (20, str(executable)),
                (9, str(executable.parent)),
                (7, name),
            ):
                _check(method(pointer, index, HRESULT, ctypes.c_wchar_p)(pointer, text))
            _check(
                method(pointer, 17, HRESULT, ctypes.c_wchar_p, ctypes.c_int32)(
                    pointer, str(executable), 0
                )
            )
            _check(
                method(persistence, 6, HRESULT, ctypes.c_wchar_p, ctypes.c_int32)(
                    persistence, str(temporary), 1
                )
            )
        # Release IPersistFile before replacing the destination on Windows.
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
