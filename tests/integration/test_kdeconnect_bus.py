"""Real isolated D-Bus transport against a synthetic, paired phone service."""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

import gi
import pytest

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from docking.applets.kdeconnect.backend import (
    DAEMON_IFACE,
    DEVICE_IFACE,
    ROOT_PATH,
    SERVICE,
    KdeConnectBackend,
)
from docking.applets.kdeconnect.state import SHARE_PLUGIN

CONTROL_IFACE = "org.docking.TestKdeConnect"
DEVICE_PATH = ROOT_PATH + "/devices/test_phone"


def _wait(predicate, timeout=5):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for private KDE Connect D-Bus state")


@pytest.fixture
def private_phone_bus():
    if not shutil.which("dbus-daemon"):
        pytest.skip("dbus-daemon is not installed")
    daemon = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert daemon.stdout is not None
    assert select.select([daemon.stdout], [], [], 5)[0], "Private D-Bus did not start"
    address = daemon.stdout.readline().strip()
    provider = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--phone-service"],
        env={**os.environ, "DBUS_SESSION_BUS_ADDRESS": address},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert provider.stdout is not None
        assert select.select([provider.stdout], [], [], 5)[0], (
            "Phone fixture did not start"
        )
        assert provider.stdout.readline().strip() == "ready"
        bus = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        yield bus, provider
        bus.close_sync(None)
    finally:
        for process in (provider, daemon):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _control(bus, method, signature=None, args=()):
    result = bus.call_sync(
        SERVICE,
        ROOT_PATH,
        CONTROL_IFACE,
        method,
        GLib.Variant(signature, args) if signature else None,
        None,
        Gio.DBusCallFlags.NONE,
        2000,
        None,
    )
    return result.unpack()


def test_private_dbus_device_battery_actions_and_owner_lifetime(
    private_phone_bus, tmp_path
):
    bus, provider = private_phone_bus
    changes = []
    backend = KdeConnectBackend(bus)
    backend.start(lambda: changes.append(True))
    try:
        _wait(lambda: bool(backend._owner))
        state = backend.snapshot()
        assert state.available and len(state.devices) == 1
        phone = state.devices[0]
        assert phone.battery == 73 and phone.supports(SHARE_PLUGIN)
        initial_changes = len(changes)
        _control(bus, "SetBattery", "(ib)", (0, True))
        _wait(lambda: len(changes) > initial_changes)
        battery = backend.snapshot().devices[0]
        assert battery.battery == 0 and battery.charging
        backend.ring(phone.device_id)
        file = tmp_path / "transfer payload.txt"
        file.write_text("synthetic transport test")
        backend.send_files(phone.device_id, (file,))
        actions = _control(bus, "Actions")[0]
        assert actions == ["ring", "share:" + file.as_uri()]
        initial_changes = len(changes)
        _control(bus, "SetReachable", "(b)", (False,))
        _wait(lambda: len(changes) > initial_changes)
        assert not backend.snapshot().devices[0].reachable
        provider.terminate()
        provider.wait(3)
        _wait(lambda: not backend._owner)
        assert not backend.snapshot().available
    finally:
        backend.close()
    assert not backend._subscriptions and backend._watch == 0


def _phone_service():
    """Keep this fixture on the private test bus, never the user's session bus."""
    loop = GLib.MainLoop()
    state = {"charge": 73, "charging": False, "reachable": True, "actions": []}
    root_xml = f"""<node>
      <interface name="{DAEMON_IFACE}">
        <method name="devices"><arg type="b" direction="in"/><arg type="b" direction="in"/><arg type="as" direction="out"/></method>
        <signal name="deviceListChanged"/>
      </interface>
      <interface name="{CONTROL_IFACE}">
        <method name="SetBattery"><arg type="i" direction="in"/><arg type="b" direction="in"/></method>
        <method name="SetReachable"><arg type="b" direction="in"/></method>
        <method name="Actions"><arg type="as" direction="out"/></method>
      </interface>
    </node>"""
    device_xml = f"""<node><interface name="{DEVICE_IFACE}">
      <method name="loadedPlugins"><arg type="as" direction="out"/></method>
      <property name="name" type="s" access="read"/>
      <property name="type" type="s" access="read"/>
      <property name="isPaired" type="b" access="read"/>
      <property name="isReachable" type="b" access="read"/>
      <signal name="reachableChanged"><arg type="b"/></signal>
    </interface></node>"""
    battery_xml = f"""<node><interface name="{DEVICE_IFACE}.battery">
      <property name="charge" type="i" access="read"/>
      <property name="isCharging" type="b" access="read"/>
      <property name="hasBattery" type="b" access="read"/>
      <signal name="refreshed"><arg type="b"/><arg type="i"/></signal>
    </interface></node>"""
    ring_xml = f"""<node><interface name="{DEVICE_IFACE}.findmyphone"><method name="ring"/></interface></node>"""
    share_xml = f"""<node><interface name="{DEVICE_IFACE}.share"><method name="shareUrl"><arg type="s" direction="in"/></method></interface></node>"""

    def method(connection, _sender, path, interface, member, params, invocation):
        args = params.unpack()
        if member == "devices":
            invocation.return_value(GLib.Variant("(as)", (["test_phone"],)))
        elif member == "loadedPlugins":
            invocation.return_value(
                GLib.Variant(
                    "(as)",
                    (
                        [
                            "kdeconnect_battery",
                            "kdeconnect_findmyphone",
                            "kdeconnect_share",
                        ],
                    ),
                )
            )
        elif member == "SetBattery":
            state["charge"], state["charging"] = args
            connection.emit_signal(
                None,
                DEVICE_PATH + "/battery",
                DEVICE_IFACE + ".battery",
                "refreshed",
                GLib.Variant("(bi)", (state["charging"], state["charge"])),
            )
            invocation.return_value(None)
        elif member == "SetReachable":
            state["reachable"] = args[0]
            connection.emit_signal(
                None, DEVICE_PATH, DEVICE_IFACE, "reachableChanged", params
            )
            invocation.return_value(None)
        elif member == "Actions":
            invocation.return_value(GLib.Variant("(as)", (state["actions"],)))
        else:
            state["actions"].append("ring" if member == "ring" else "share:" + args[0])
            invocation.return_value(None)

    def prop(_connection, _sender, _path, interface, name):
        values = {
            "name": GLib.Variant("s", "Synthetic Phone"),
            "type": GLib.Variant("s", "phone"),
            "isPaired": GLib.Variant("b", True),
            "isReachable": GLib.Variant("b", state["reachable"]),
            "charge": GLib.Variant("i", state["charge"]),
            "isCharging": GLib.Variant("b", state["charging"]),
            "hasBattery": GLib.Variant("b", True),
        }
        return values[name]

    def acquired(connection, _name):
        for path, xml in (
            (ROOT_PATH, root_xml),
            (DEVICE_PATH, device_xml),
            (DEVICE_PATH + "/battery", battery_xml),
            (DEVICE_PATH + "/findmyphone", ring_xml),
            (DEVICE_PATH + "/share", share_xml),
        ):
            for interface in Gio.DBusNodeInfo.new_for_xml(xml).interfaces:
                connection.register_object(path, interface, method, prop, None)

    Gio.bus_own_name(
        Gio.BusType.SESSION,
        SERVICE,
        Gio.BusNameOwnerFlags.NONE,
        acquired,
        lambda *_args: print("ready", flush=True),
        None,
    )
    loop.run()


if __name__ == "__main__" and "--phone-service" in sys.argv:
    _phone_service()
