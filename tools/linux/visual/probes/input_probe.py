"""Persistent virtual pointer; requests and acknowledgements use a private socket."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import os
import socket
import time
from pathlib import Path


def connect_pointer():
    from generated.wlr_virtual_pointer_unstable_v1 import (
        ZwlrVirtualPointerManagerV1,
    )
    from pywayland.client import Display

    display = Display()
    display.connect()
    registry = display.get_registry()
    managers = []

    def advertised(registry, name, interface, version):
        if interface == "zwlr_virtual_pointer_manager_v1":
            managers.append(
                registry.bind(name, ZwlrVirtualPointerManagerV1, min(version, 2))
            )

    registry.dispatcher["global"] = advertised
    display.roundtrip()
    if not managers:
        registry.destroy()
        display.disconnect()
        raise RuntimeError("compositor does not advertise virtual-pointer")
    pointer = managers[0].create_virtual_pointer(None)
    display.roundtrip()
    return display, pointer


class X11Pointer:
    """Input on the private Xvfb parent; GTK delivery is verified in the child."""

    def __init__(self, name: str, authority: str):
        os.environ["XAUTHORITY"] = authority
        self.xlib = ctypes.CDLL(ctypes.util.find_library("X11"))
        self.xtst = ctypes.CDLL(ctypes.util.find_library("Xtst"))
        self.xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.xlib.XOpenDisplay.restype = ctypes.c_void_p
        self.display = self.xlib.XOpenDisplay(name.encode())
        if not self.display:
            raise RuntimeError("cannot open private Xvfb parent")
        for name in ("XDisplayWidth", "XDisplayHeight"):
            function = getattr(self.xlib, name)
            function.argtypes = [ctypes.c_void_p, ctypes.c_int]
            function.restype = ctypes.c_int
        self.width = self.xlib.XDisplayWidth(self.display, 0)
        self.height = self.xlib.XDisplayHeight(self.display, 0)
        self.xtst.XTestFakeMotionEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self.xtst.XTestFakeButtonEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self.xlib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]

    def motion_absolute(self, now, x, y, width, height):
        self.xtst.XTestFakeMotionEvent(
            self.display, 0, x * self.width // width, y * self.height // height, 0
        )

    def button(self, now, button, state):
        self.xtst.XTestFakeButtonEvent(
            self.display, {272: 1, 273: 3, 274: 2}[button], state, 0
        )

    def frame(self):
        self.roundtrip()

    def axis(self, now, axis, value):
        button = 5 if value > 0 else 4
        for _ in range(max(1, round(abs(value) / 15))):
            self.xtst.XTestFakeButtonEvent(self.display, button, 1, 0)
            self.xtst.XTestFakeButtonEvent(self.display, button, 0, 0)

    def roundtrip(self):
        self.xlib.XSync(self.display, 0)


def serve(path: Path, x11_display: str | None, authority: str) -> None:
    if x11_display:
        pointer = X11Pointer(x11_display, authority)
        display = pointer
    else:
        display, pointer = connect_pointer()
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        path.chmod(0o600)
        server.listen(1)
        while True:
            with server.accept()[0] as client:
                request = json.loads(client.recv(4096))
                now = int(time.monotonic() * 1000) & 0xFFFFFFFF
                pointer.motion_absolute(
                    now, request["x"], request["y"], request["width"], request["height"]
                )
                if request.get("button"):
                    pointer.button(now, request["button"], 1)
                    pointer.frame()
                    pointer.button(now, request["button"], 0)
                if request.get("scroll"):
                    pointer.axis(now, 0, request["scroll"] * 15.0)
                pointer.frame()
                display.roundtrip()
                client.sendall(b'{"ok":true}\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("socket", type=Path, nargs="?")
    parser.add_argument("--globals", action="store_true")
    parser.add_argument("--send", nargs="+", type=int)
    parser.add_argument("--x11-display")
    parser.add_argument("--x11-authority", default="")
    args = parser.parse_args()
    if args.globals:
        from pywayland.client import Display

        display = Display()
        display.connect()
        registry = display.get_registry()
        names = []
        registry.dispatcher["global"] = lambda registry, name, interface, version: (
            names.append(interface)
        )
        display.roundtrip()
        print(json.dumps(names))
        registry.destroy()
        display.disconnect()
        return
    if args.socket is None:
        parser.error("socket path required")
    if args.send:
        x, y, width, height, *button = args.send
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(str(args.socket))
            client.sendall(
                json.dumps(
                    dict(
                        zip(
                            ("x", "y", "width", "height", "button"),
                            (x, y, width, height, *button),
                            strict=False,
                        )
                    )
                ).encode()
            )
            if json.loads(client.recv(4096)).get("ok") is not True:
                raise RuntimeError("pointer request not acknowledged")
    else:
        serve(args.socket, args.x11_display, args.x11_authority)


if __name__ == "__main__":
    main()
