"""Recycle Bin folder selection for the shared native Shell observer."""

from collections.abc import Callable

from gi.repository import GLib as GLib

from docking.platform.native.windows.shell_changes import ShellChangeMonitor


class RecycleBinMonitor(ShellChangeMonitor):
    def __init__(self, changed: Callable[[], None]) -> None:
        super().__init__(
            changed,
            folder_id="b7534046-3ecb-4c18-be4e-64cd4cb7d6ac",
            title="Docking Recycle Bin monitor",
        )
