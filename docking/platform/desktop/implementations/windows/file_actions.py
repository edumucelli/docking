"""Interactive Windows file actions, independent of GTK menu ownership."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import cast

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from docking.applets.worker import BackgroundWorker
from docking.core.paths import local_path_from_uri_or_path
from docking.log import get_logger
from docking.platform.desktop.base import OpenWithResult
from docking.platform.native.windows.ffi import window_handle

log = get_logger("windows.file_actions")


def open_in_terminal(path: Path) -> bool:
    system_root = os.environ.get("SYSTEMROOT")
    if not system_root:
        return False
    executable = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        subprocess.Popen(
            [str(executable), "-NoLogo"],
            cwd=str(path),
            shell=False,
            creationflags=0x00000010,  # CREATE_NEW_CONSOLE preserves interactive IO.
        )
    except OSError as exc:
        log.warning("Could not open terminal: %s", exc)
        return False
    return True


def request_open_with(
    uri: str,
    *,
    parent: object,
    on_result: Callable[[OpenWithResult], None],
) -> Callable[[], None]:
    from docking.platform.native.windows.shell import open_with_dialog

    window = cast(Gtk.Window, parent).get_window()
    owner = window_handle(window) if window is not None else None
    path = local_path_from_uri_or_path(uri)
    cancelled = Event()

    def open_dialog() -> OpenWithResult:
        if cancelled.is_set():
            return OpenWithResult.CANCELLED
        if path is None or owner is None or not path.is_file():
            return OpenWithResult.FAILED
        if cancelled.is_set():
            return OpenWithResult.CANCELLED
        return open_with_dialog(str(path), owner)

    def completed(result: OpenWithResult) -> None:
        if not cancelled.is_set():
            on_result(result)

    BackgroundWorker().run(
        name="windows-open-with",
        fn=open_dialog,
        on_result=completed,
        on_error=lambda error: completed(OpenWithResult.FAILED),
    )
    # The shell owns the picker window and can keep it open after shutdown.
    return cancelled.set
