"""Network applet public API."""

from .applet import NM, GLib, Gtk, NetworkApplet, time  # noqa: F401
from .state import (
    TrafficCounters,
    TrafficSpeeds,
    compute_speeds,
    format_speed,
    parse_proc_net_dev,
    signal_to_icon,
)

__all__ = [
    "NetworkApplet",
    "TrafficCounters",
    "TrafficSpeeds",
    "compute_speeds",
    "format_speed",
    "parse_proc_net_dev",
    "signal_to_icon",
    "time",
]
