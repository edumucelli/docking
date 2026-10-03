"""Apply a temporary native Mutter monitor configuration in the private Shell."""

import sys

from gi.repository import Gio, GLib

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)


def request(method, parameters=None):
    return bus.call_sync(
        "org.gnome.Mutter.DisplayConfig",
        "/org/gnome/Mutter/DisplayConfig",
        "org.gnome.Mutter.DisplayConfig",
        method,
        parameters,
        None,
        Gio.DBusCallFlags.NONE,
        5000,
        None,
    ).unpack()


serial, monitors, logical, properties = request("GetCurrentState")
scene = sys.argv[1]
config = []
x = 0
for index, (spec, modes, _props) in enumerate(monitors):
    mode = next((mode for mode in modes if mode[-1].get("is-current")), modes[0])
    scale = (
        1.25 if scene == "fractional" or (scene == "mixed-dpi" and index == 0) else 1.0
    )
    if scale not in mode[5]:
        raise RuntimeError(
            f"Mutter does not support scale {scale} on {spec[0]}: {mode[5]}"
        )
    config.append((x, 0, scale, 0, index == 0, [(spec[0], mode[0], {})]))
    x += round(mode[1] / scale)
request(
    "ApplyMonitorsConfig",
    GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", (serial, 1, config, {})),
)
