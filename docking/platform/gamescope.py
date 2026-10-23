"""Process environment preparation for native clients nested in GameScope."""

from __future__ import annotations

import os


def prepare_gamescope_wayland_environment() -> bool:
    """Point GTK at GameScope's private Wayland socket before GTK is imported.

    GameScope always publishes ``GAMESCOPE_WAYLAND_DISPLAY`` but normally keeps
    ``WAYLAND_DISPLAY`` unset and advertises an X11 session to child processes.
    Its layer-shell global is nevertheless available on the private socket.
    Explicit user choices for ``WAYLAND_DISPLAY`` and ``GDK_BACKEND`` win.
    """
    display = os.environ.get("GAMESCOPE_WAYLAND_DISPLAY", "").strip()
    if not display:
        return False
    os.environ.setdefault("WAYLAND_DISPLAY", display)
    os.environ.setdefault("GDK_BACKEND", "wayland")
    return True
