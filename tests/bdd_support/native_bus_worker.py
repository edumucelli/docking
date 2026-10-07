"""Real Unix-FD/idle contracts on a dbus-run-session-owned bus, never the host."""

import json
import os
import sys
import threading
import time
from pathlib import Path

from gi.repository import Gio, GLib

from docking.platform.backends.base import DisplayServer, WindowId
from docking.platform.backends.dbus_idle import MutterIdleService
from docking.platform.backends.kwin.preview import KWinPreviewService

XML = """<node>
<interface name="org.kde.KWin.ScreenShot2"><method name="CaptureWindow">
<arg type="s" direction="in"/><arg type="a{sv}" direction="in"/>
<arg type="h" direction="in"/><arg type="a{sv}" direction="out"/>
</method></interface>
<interface name="org.gnome.Mutter.IdleMonitor"><method name="GetIdletime">
<arg type="t" direction="out"/></method></interface>
</node>"""
ID = "{11111111-2222-3333-4444-555555555555}"


def main(mode):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    for name in ("org.kde.KWin", "org.gnome.Mutter.IdleMonitor"):
        bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "RequestName",
            GLib.Variant("(su)", (name, 4)),
            GLib.VariantType.new("(u)"),
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
    workers = []
    calls = []

    def method(_bus, _sender, _path, _iface, name, args, invocation):
        calls.append(name)
        if name == "GetIdletime":
            if mode == "idle-failed":
                invocation.return_dbus_error(
                    "org.gnome.Mutter.IdleMonitor.Unavailable", "Unavailable"
                )
            else:
                invocation.return_value(GLib.Variant("(t)", (12500,)))
            return
        if mode == "denied":
            invocation.return_dbus_error(
                "org.kde.KWin.ScreenShot2.Error.NoAuthorized", "Denied"
            )
            return
        if mode == "timeout":
            return
        identifier, _options, index = args.unpack()
        assert identifier == ID and index == 0
        fd = invocation.get_message().get_unix_fd_list().get(index)
        width, height = 320, 200
        data = bytes([0, 0, 255, 255]) * width * height
        metadata = {
            "type": "raw",
            "format": 6,
            "width": width,
            "height": height,
            "stride": width * 4,
            "windowId": identifier,
        }
        if mode == "wrong-window":
            metadata["windowId"] = "another-window"
        if mode == "truncated":
            data = data[:8]
        if mode == "oversized":
            metadata["stride"] = 1024 * 1024
        invocation.return_value(
            GLib.Variant(
                "(a{sv})",
                (
                    {
                        key: GLib.Variant("s" if isinstance(value, str) else "u", value)
                        for key, value in metadata.items()
                    },
                ),
            )
        )

        def write():
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
            except BrokenPipeError:
                pass
            finally:
                os.close(fd)

        worker = threading.Thread(target=write, daemon=True)
        workers.append(worker)
        worker.start()

    info = Gio.DBusNodeInfo.new_for_xml(XML)
    registration = bus.register_object(
        "/org/kde/KWin/ScreenShot2", info.interfaces[0], method, None, None
    )
    idle_registration = bus.register_object(
        "/org/gnome/Mutter/IdleMonitor/Core", info.interfaces[1], method, None, None
    )
    ticks = []
    timer = GLib.timeout_add(5, lambda: ticks.append(True) or True)
    previews = KWinPreviewService()
    try:
        previews.start()
        before = len(list(Path("/proc/self/fd").iterdir()))
        start = time.monotonic()
        preview = previews.capture(
            WindowId(DisplayServer.WAYLAND, f"kwin:{ID}"), width=160, height=100
        )
        elapsed = time.monotonic() - start
        for worker in workers:
            worker.join(timeout=1)
        if mode.startswith("idle"):
            # Proxy sync creation cannot dispatch its own server in the same
            # thread. Exercise GetIdletime through an independent bus client.
            proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
                None,
                "org.gnome.Mutter.IdleMonitor",
                "/org/gnome/Mutter/IdleMonitor/Core",
                "org.gnome.Mutter.IdleMonitor",
                None,
            )
            result = []
            worker = threading.Thread(
                target=lambda: result.append(MutterIdleService(proxy).idle_seconds())
            )
            worker.start()
            while worker.is_alive():
                GLib.MainContext.default().iteration(False)
            worker.join()
            print(json.dumps({"idle": result[0]}), flush=True)
            return
        assert (preview is not None) == (mode == "valid"), (mode, preview)
        if preview:
            assert preview.width == 160 and preview.height == 100
            assert list(preview.image.get_pixels())[:3] == [255, 0, 0]
        assert elapsed < 0.8, elapsed
        assert calls == ["CaptureWindow"], calls
        after = len(list(Path("/proc/self/fd").iterdir()))
        assert after <= before + 1, (before, after)
        print(
            json.dumps(
                {
                    "mode": mode,
                    "preview": preview is not None,
                    "elapsed": elapsed,
                    "ticks": len(ticks),
                    "fd_delta": after - before,
                }
            ),
            flush=True,
        )
    finally:
        previews.stop()
        GLib.source_remove(timer)
        bus.unregister_object(registration)
        bus.unregister_object(idle_registration)


if __name__ == "__main__":
    main(sys.argv[1])
