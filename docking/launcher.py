"""Launch Docking after backend-specific display setup."""

from __future__ import annotations

import sys


def focus_running_dock() -> bool:
    """Request keyboard navigation without starting a second dock process."""
    from gi.repository import Gio, GLib

    from docking.ipc.introspection import BUS_NAME, ITEMS_INTERFACE, OBJECT_PATH

    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = connection.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            ITEMS_INTERFACE,
            "Focus",
            None,
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            3000,
            None,
        )
        return bool(result.unpack()[0])
    except GLib.Error as exc:
        print(f"Could not focus Docking: {exc.message}", file=sys.stderr)
        return False


def prepare_display() -> bool:
    """Let a backend prepare GTK before the application module loads."""
    from docking.platform.backends.cinnamon.startup import prepare_cinnamon_display

    return prepare_cinnamon_display()


def main() -> None:
    """Prepare the platform display, then start the dock runtime."""
    if sys.argv[1:] == ["--focus"]:
        raise SystemExit(0 if focus_running_dock() else 1)
    prepare_display()
    from docking.app import main as run_app

    run_app()


if __name__ == "__main__":
    main()
