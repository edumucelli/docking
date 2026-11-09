"""Python signal forwarding into GTK with reversible handler ownership."""

from __future__ import annotations

import signal
from collections.abc import Callable


def install_quit_handlers(quit: Callable[[], bool]) -> Callable[[], None]:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    active = True

    def dispatch() -> bool:
        return quit() if active else False

    def on_signal(_signum: int, _frame: object) -> None:
        if active:
            GLib.idle_add(dispatch)

    previous = {
        signum: signal.signal(signum, on_signal)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }

    def cleanup() -> None:
        nonlocal active
        if not active:
            return
        active = False
        for signum, handler in previous.items():
            if signal.getsignal(signum) is on_signal:
                signal.signal(signum, handler)

    return cleanup
