"""Unix GUI lifecycle hooks. Imported after toolkit initialization."""

from __future__ import annotations

import signal
from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib


def install_quit_handlers(quit: Callable[[], bool]) -> Callable[[], None]:
    sources: dict[signal.Signals, int] = {}

    def requested(signum: signal.Signals) -> bool:
        keep = False
        try:
            keep = quit()
        finally:
            # GLib removes a source whose callback returns False (or raises).
            # Cleanup must only remove signal sources that are still installed.
            if not keep:
                sources.pop(signum, None)
        return keep

    for sig in (signal.SIGINT, signal.SIGTERM):
        sources[sig] = GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, requested, sig)

    def remove() -> None:
        for source in sources.values():
            GLib.source_remove(source)
        sources.clear()

    return remove
