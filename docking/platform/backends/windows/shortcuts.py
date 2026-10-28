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
from .properties import APP_ID, PROPERTY_STORE, PropertyKey, PropertyVariant


@dataclass(frozen=True, slots=True)
class ShellLink:
    executable: Path | None
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
                persistence, str(path.resolve()).replace("/", "\\"), 0
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
        values = []
        for index in (8, 10):  # GetWorkingDirectory, GetArguments
            buffer = ctypes.create_unicode_buffer(32768)
            _check(
                method(pointer, index, HRESULT, ctypes.c_wchar_p, ctypes.c_int32)(
                    pointer, buffer, len(buffer)
                )
            )
            values.append(buffer.value)
        return ShellLink(
            Path(target.value) if target.value else None, values[0], values[1]
        )


@contextmanager
def _saved_link(path: Path) -> Iterator[ctypes.c_void_p]:
    """Publish a complete link only after all native configuration succeeds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".lnk", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(filename)
    try:
        with _link() as (pointer, persistence):
            yield pointer
            _check(
                method(persistence, 6, HRESULT, ctypes.c_wchar_p, ctypes.c_int32)(
                    persistence, str(temporary).replace("/", "\\"), 1
                )
            )
        # Release IPersistFile before replacing the destination on Windows.
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_shortcut(path: Path, *, executable: Path, name: str) -> None:
    """Atomically persist a direct executable launcher with its own directory."""
    with _saved_link(path) as pointer:
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


def write_application_shortcut(
    path: Path, *, target: str, application_id: str, name: str
) -> None:
    """Write an AppsFolder namespace link on a worker, without a launch command."""
    with apartment() as ole:
        pidl = ctypes.c_void_p()
        parse = bind(
            ctypes.WinDLL("shell32", use_last_error=True),
            "SHParseDisplayName",
            HRESULT,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        )
        try:
            _check(parse(target, None, ctypes.byref(pidl), 0, None))
            if not pidl.value:
                raise OSError("Missing shell application target")
            with _saved_link(path) as pointer, ExitStack() as properties:
                _check(method(pointer, 5, HRESULT, ctypes.c_void_p)(pointer, pidl))
                _check(method(pointer, 7, HRESULT, ctypes.c_wchar_p)(pointer, name))
                store = query(properties, pointer, PROPERTY_STORE)
                identity = PropertyVariant()
                identity.vt = 31  # VT_LPWSTR, caller-owned Python string.
                identity.data.string = application_id
                _check(
                    method(
                        store,
                        6,
                        HRESULT,
                        ctypes.POINTER(PropertyKey),
                        ctypes.POINTER(PropertyVariant),
                    )(store, ctypes.byref(APP_ID), ctypes.byref(identity))
                )
                _check(method(store, 7, HRESULT)(store))
        finally:
            if pidl.value:
                bind(ole, "CoTaskMemFree", None, ctypes.c_void_p)(pidl)
