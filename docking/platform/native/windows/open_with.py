"""Present the Windows application picker with a cancellable WinRT operation."""

from __future__ import annotations

import ctypes
from contextlib import ExitStack
from threading import Event

from docking.log import get_logger
from docking.platform.desktop.base import OpenWithResult
from docking.platform.native.windows.com import (
    GUID,
    HRESULT,
    method,
    object_result,
    query,
    value,
)
from docking.platform.native.windows.winrt import POINTER, WindowsRuntime, check

_STORAGE_FILE_STATICS = GUID.parse("5984c710-daf2-43c8-8bb4-a4d3eacfd03f")
_LAUNCHER_STATICS = GUID.parse("277151c3-9e3e-42f6-91a4-5dfdeb232451")
_ACTIVATION_FACTORY = GUID.parse("00000035-0000-0000-c000-000000000046")
_LAUNCHER_OPTIONS = GUID.parse("bafa21d8-b071-4cd8-853e-341203e557d3")
_INITIALIZE_WITH_WINDOW = GUID.parse("3e68d4bd-7135-4d10-8018-9fb6d9f33fa1")
log = get_logger("windows.open_with")


def open_with_dialog(path: str, owner: int, cancelled: Event) -> OpenWithResult:
    """Open the exact file with the selected handler on the calling worker."""
    try:
        with WindowsRuntime() as runtime, ExitStack() as scope:
            storage = runtime.factory(
                scope, "Windows.Storage.StorageFile", _STORAGE_FILE_STATICS
            )
            # MSYS2 paths may have forward separators, which the picker rejects.
            with runtime.string(path.replace("/", "\\")) as name:
                lookup = object_result(
                    scope, storage, 6, POINTER, name
                )  # GetFileFromPathAsync
            runtime.wait(scope, lookup, cancelled)
            file = object_result(scope, lookup, 8)  # GetResults: IStorageFile

            factory = runtime.factory(
                scope, "Windows.System.LauncherOptions", _ACTIVATION_FACTORY
            )
            options = object_result(scope, factory, 6)  # ActivateInstance
            owned = query(scope, options, _INITIALIZE_WITH_WINDOW)
            check(method(owned, 3, HRESULT, POINTER)(owned, owner))  # Initialize
            options = query(scope, options, _LAUNCHER_OPTIONS)
            check(
                method(options, 9, HRESULT, ctypes.c_bool)(options, True)
            )  # DisplayApplicationPicker

            launcher = runtime.factory(
                scope, "Windows.System.Launcher", _LAUNCHER_STATICS
            )
            if cancelled.is_set():
                return OpenWithResult.CANCELLED
            # LaunchFileAsync(file, options) completes after the user's choice.
            opened = object_result(scope, launcher, 7, POINTER, file, POINTER, options)
            runtime.wait(scope, opened, cancelled, timeout=None)
            launched = value(opened, 8, ctypes.c_bool).value  # GetResults
            return OpenWithResult.ACCEPTED if launched else OpenWithResult.CANCELLED
    except OSError as exc:
        if cancelled.is_set() or exc.errno == 0x800704C7:
            return OpenWithResult.CANCELLED
        log.warning("Application chooser failed: %s", exc)
        return OpenWithResult.FAILED
