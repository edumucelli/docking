"""Lab-only stimuli and GTK observations in the unmodified Docking process.

Backend calls are stimuli, never the oracle: the host compares them with a
separate compositor connection. This socket exists only in the private lab.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import os
import socket
import sys
from pathlib import Path


def call(path, request):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(8)
        client.connect(path)
        client.sendall(json.dumps(request).encode() + b"\n")
        with client.makefile("rb") as stream:
            result = json.loads(stream.readline())
    if "error" in result:
        raise RuntimeError(result["error"])
    return result


def serialize(value):
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, enum.Enum):
        return value.value
    raise TypeError(type(value).__name__)


def serve(path):
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    from docking import app

    backend = None
    ui = None
    factory = app.create_session_backend

    def capture_backend(**kwargs):
        nonlocal backend
        backend = factory(**kwargs)
        return backend

    app.create_session_backend = capture_backend
    ui_factory = app.build_dock_window

    def capture_ui(**kwargs):
        nonlocal ui
        ui = ui_factory(**kwargs)
        return ui

    app.build_dock_window = capture_ui
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(path)
    Path(path).chmod(0o600)
    listener.listen(4)

    def request_ready(*unused):
        with listener.accept()[0] as client:
            client.settimeout(0.5)
            try:
                with client.makefile("rb") as stream:
                    request = json.loads(stream.readline(8192))
                if backend is None:
                    raise RuntimeError("Docking backend not ready")
                action = request.get("action", "snapshot")
                desktop_id = request.get("desktop_id", "lab-probe.desktop")
                windows = backend.windows.list_windows(desktop_id)
                result = None
                if action == "minimize":
                    result = backend.windows.minimize_all(desktop_id)
                elif action in {"activate", "close"}:
                    if not windows:
                        raise RuntimeError("test window not tracked")
                    result = getattr(backend.windows, action)(windows[0].id)
                elif action == "workspace":
                    result = backend.workspaces.activate(request["id"])
                elif action == "position":
                    ui.window.config.position = request["edge"]
                    ui.window.placement.reposition()
                elif action != "snapshot":
                    raise ValueError(f"unknown lab action {action}")
                popups = []
                for window in Gtk.Window.list_toplevels():
                    if not window.get_mapped() or window.get_title() == "Docking":
                        continue
                    surface = window.get_window()
                    if surface is not None:
                        popups.append(
                            {
                                "class": type(window).__name__,
                                "kind": surface.get_type_hint().value_nick,
                                "width": surface.get_width(),
                                "height": surface.get_height(),
                                "scale": surface.get_scale_factor(),
                            }
                        )
                        requested_kind = request.get("popup_kind")
                        matches = (
                            type(window).__name__ == "PreviewPopup"
                            if requested_kind == "preview"
                            else type(window).__name__ != "PreviewPopup"
                            and surface.get_type_hint().value_nick
                            == ("popup-menu" if requested_kind == "menu" else "tooltip")
                        )
                        if request.get("capture_popup") and matches:
                            import cairo

                            image = cairo.ImageSurface(
                                cairo.FORMAT_ARGB32,
                                surface.get_width(),
                                surface.get_height(),
                            )
                            window.draw(cairo.Context(image))
                            image.write_to_png(request["capture_popup"])
                response = {
                    "pid": os.getpid(),
                    "backend": backend.name,
                    "dock_allocation": [
                        ui.window.get_allocated_width(),
                        ui.window.get_allocated_height(),
                    ],
                    "dock_mapped": ui.window.get_mapped(),
                    "result": result,
                    "windows": backend.windows.list_windows(desktop_id),
                    "workspaces": backend.workspaces.list_workspaces()
                    if backend.workspaces
                    else [],
                    "popups": popups,
                }
                item = ui.window.model.find_by_desktop_id(desktop_id=desktop_id)
                if item is not None:
                    from docking.ui.display import window_screen_position

                    geometry = ui.window.geometry.build_frame().geometry_for_item(item)
                    if geometry is not None:
                        position = window_screen_position(ui.window)
                        rect = geometry.draw_rect
                        response["pointer"] = [
                            round(position.x + rect.x + rect.w / 2),
                            round(position.y + rect.y + rect.h / 2),
                        ]
                payload = json.dumps(response, default=serialize)
            except Exception as exc:
                payload = json.dumps({"error": str(exc)})
            client.sendall(payload.encode() + b"\n")
        return True

    GLib.io_add_watch(listener.fileno(), GLib.IO_IN, request_ready)
    try:
        app.main()
    finally:
        listener.close()
        Path(path).unlink()


if __name__ == "__main__":
    if sys.argv[1] == "--call":
        print(json.dumps(call(sys.argv[2], json.loads(sys.argv[3]))))
    else:
        serve(sys.argv[1])
