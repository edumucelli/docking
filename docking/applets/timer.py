"""Timers owned by applets on the GLib main loop."""

from collections.abc import Callable

from gi.repository import GLib


class OneShotTimer:
    """Own one pending callback and allow cancellation before it runs."""

    def __init__(self) -> None:
        self._source_id = 0

    def schedule(self, delay_s: int, callback: Callable[[], None]) -> None:
        """Replace the pending callback with one scheduled after the delay."""
        self.cancel()

        def run() -> bool:
            self._source_id = 0
            callback()
            return False

        self._source_id = GLib.timeout_add_seconds(delay_s, run)

    def cancel(self) -> None:
        if self._source_id:
            GLib.source_remove(self._source_id)
            self._source_id = 0
