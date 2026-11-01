"""Recycle Bin state and user actions through the public Windows shell APIs."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from pathlib import PureWindowsPath

from gi.repository import Gio

from docking.applets.trash.backend import TrashBackend, TrashEmptyResult
from docking.core.paths import local_path_from_uri_or_path
from docking.log import get_logger

log = get_logger("trash.windows")
from docking.platform.native.windows.com import (
    GUID,
    HRESULT,
    apartment,
    method,
    release,
)
from docking.platform.native.windows.ffi import bind as _bind
from docking.platform.native.windows.shell import open_default_target


class RecycleBinInfo(ctypes.Structure):
    # Shellapi.h packs at one byte only on 32-bit Windows.
    _pack_ = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 1
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("bytes", ctypes.c_int64),
        ("items", ctypes.c_int64),
    ]


def _check(status: int) -> None:
    if status < 0:
        if status & 0xFFFFFFFF == 0x80070005:
            raise PermissionError("Windows denied the Recycle Bin operation")
        raise OSError(f"Recycle Bin operation failed: 0x{status & 0xFFFFFFFF:08x}")


class WindowsRecycleBin:
    def __init__(self):
        self._shell = ctypes.WinDLL("shell32", use_last_error=True)
        self._query = _bind(
            self._shell,
            "SHQueryRecycleBinW",
            HRESULT,
            ctypes.c_wchar_p,
            ctypes.POINTER(RecycleBinInfo),
        )
        self._empty = _bind(
            self._shell,
            "SHEmptyRecycleBinW",
            HRESULT,
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        )
        self._drive_type = _bind(
            ctypes.WinDLL("kernel32", use_last_error=True),
            "GetDriveTypeW",
            ctypes.c_uint32,
            ctypes.c_wchar_p,
        )

    def count(self) -> int:
        info = RecycleBinInfo()
        info.size = ctypes.sizeof(info)
        _check(self._query(None, ctypes.byref(info)))
        return int(info.items)

    def empty(self) -> None:
        # The shared applet already obtained explicit confirmation.
        _check(self._empty(None, None, 7))  # no confirmation/progress/sound

    def recycle(self, filename: str) -> bool:
        path = PureWindowsPath(filename)
        if (
            not path.is_absolute()
            or path.drive.startswith(("\\\\", "//"))
            or self._drive_type(path.anchor.replace("/", "\\")) not in (2, 3)
        ):
            return False  # Network shares do not have a client-side Recycle Bin.
        with apartment() as ole:
            operation, item = ctypes.c_void_p(), ctypes.c_void_p()
            try:
                clsid = GUID.parse("3ad05575-8857-4850-9277-11b85bdb8e09")
                iid = GUID.parse("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8")
                create = _bind(
                    ole,
                    "CoCreateInstance",
                    HRESULT,
                    ctypes.POINTER(GUID),
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(ctypes.c_void_p),
                )
                _check(
                    create(
                        ctypes.byref(clsid),
                        None,
                        1,
                        ctypes.byref(iid),
                        ctypes.byref(operation),
                    )
                )
                # Recycle, retain undo, stop on errors, and preserve Windows'
                # warning if recycling is impossible. Never call DeleteFile.
                flags = 0x00080000 | 0x20000000 | 0x00100000 | 0x4000 | 0x0400 | 0x0004
                _check(method(operation, 5, HRESULT, ctypes.c_uint32)(operation, flags))
                shell_item = GUID.parse("43826d1e-e718-42ee-bc55-a1e261c37bfe")
                parse = _bind(
                    self._shell,
                    "SHCreateItemFromParsingName",
                    HRESULT,
                    ctypes.c_wchar_p,
                    ctypes.c_void_p,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(ctypes.c_void_p),
                )
                _check(
                    parse(filename, None, ctypes.byref(shell_item), ctypes.byref(item))
                )
                _check(
                    method(operation, 18, HRESULT, ctypes.c_void_p, ctypes.c_void_p)(
                        operation, item, None
                    )
                )
                _check(method(operation, 21, HRESULT)(operation))
                aborted = ctypes.c_int32()
                _check(
                    method(operation, 22, HRESULT, ctypes.POINTER(ctypes.c_int32))(
                        operation, ctypes.byref(aborted)
                    )
                )
                return not aborted.value
            finally:
                release(item)
                release(operation)


class WindowsTrashBackend(TrashBackend):
    name = "windows"
    uri = "shell:RecycleBinFolder"

    def __init__(self, *, native: WindowsRecycleBin | None = None) -> None:
        self._native = native if native is not None else WindowsRecycleBin()
        self._last_count = 0

    def count_items(self) -> int:
        try:
            self._last_count = self._native.count()
        except OSError as exc:
            log.debug("Could not query Windows Recycle Bin: %s", exc)
        return self._last_count

    def monitor_files(self) -> tuple[Gio.File, ...]:
        return ()  # The virtual shell folder has no ordinary filesystem path.

    def watch_changes(self, changed: Callable[[], None]) -> Callable[[], None] | None:
        from docking.platform.native.windows.shell_changes import ShellChangeMonitor

        try:
            observer = ShellChangeMonitor(
                changed,
                folder_id="b7534046-3ecb-4c18-be4e-64cd4cb7d6ac",
                title="Docking Recycle Bin monitor",
            )
        except OSError as exc:
            log.warning("Could not monitor Windows Recycle Bin: %s", exc)
            return None
        return observer.close

    def trash_uris(self, uris: list[str]) -> bool:
        moved = False
        for uri in uris:
            path = local_path_from_uri_or_path(uri.strip())
            if path is None or not path.is_absolute():
                continue
            try:
                moved = self._native.recycle(str(path)) or moved
            except OSError as exc:
                log.debug("Could not recycle a dropped item: %s", exc)
        return moved

    def trash_file(self, file: Gio.File) -> bool:
        filename = file.get_path()
        return bool(filename and self._native.recycle(filename))

    def open(self) -> None:
        open_default_target(self.uri)

    def empty(self, confirm: Callable[[], bool]) -> TrashEmptyResult:
        before = self._last_count
        try:
            before = self._native.count()
            self._last_count = before
            if not before or not confirm():
                return TrashEmptyResult(False, before, before)
            self._native.empty()
            remaining = self._native.count()
            self._last_count = remaining
            return TrashEmptyResult(True, before, remaining)
        except OSError as exc:
            log.warning("Could not empty Windows Recycle Bin: %s", exc)
            return TrashEmptyResult(
                True,
                before,
                self.count_items(),
                failures=1,
                permission_denied=isinstance(exc, PermissionError),
            )
