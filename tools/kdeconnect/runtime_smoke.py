"""Verify the applet against a real KDE Connect daemon on a private session bus.

Run in the disposable container described in README.md. No host desktop,
physical phone, or user's session bus is accessed.
"""

from __future__ import annotations

import os
import select
import subprocess
import tempfile
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from docking.applets.kdeconnect.backend import (
    DAEMON_IFACE,
    ROOT_PATH,
    SERVICE,
    KdeConnectBackend,
)


def wait(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(0.01)
    raise RuntimeError("Timed out waiting for real KDE Connect D-Bus state")


def main():
    processes = []
    backend = None
    with tempfile.TemporaryDirectory(prefix="docking-kdeconnect-") as directory:
        root = Path(directory)
        paths = {
            "HOME": root / "home",
            "XDG_CONFIG_HOME": root / "config",
            "XDG_DATA_HOME": root / "data",
            "XDG_CACHE_HOME": root / "cache",
            "XDG_RUNTIME_DIR": root / "runtime",
        }
        for path in paths.values():
            path.mkdir(mode=0o700)
        env = {
            **os.environ,
            **{key: str(path) for key, path in paths.items()},
            "QT_QPA_PLATFORM": "offscreen",
            "LANG": "C.UTF-8",
        }
        with (root / "daemon.log").open("w") as log:
            try:
                daemon_bus = subprocess.Popen(
                    ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                )
                processes.append(daemon_bus)
                assert daemon_bus.stdout is not None
                assert select.select([daemon_bus.stdout], [], [], 5)[0]
                address = daemon_bus.stdout.readline().strip()
                env["DBUS_SESSION_BUS_ADDRESS"] = address
                bus = Gio.DBusConnection.new_for_address_sync(
                    address,
                    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                    None,
                    None,
                )
                # Start explicitly, with an isolated profile. Waiting for the
                # actual owner avoids auto-activating a second profile.
                process = subprocess.Popen(
                    ["kdeconnectd"], env=env, stdout=log, stderr=subprocess.STDOUT
                )
                processes.append(process)

                def has_owner():
                    return bus.call_sync(
                        "org.freedesktop.DBus",
                        "/org/freedesktop/DBus",
                        "org.freedesktop.DBus",
                        "NameHasOwner",
                        GLib.Variant("(s)", (SERVICE,)),
                        None,
                        Gio.DBusCallFlags.NONE,
                        2000,
                        None,
                    ).unpack()[0]

                wait(has_owner)
                backend = KdeConnectBackend(bus)
                changes = []
                backend.start(lambda: changes.append(True))
                wait(lambda: backend.snapshot().available)
                state = backend.snapshot()
                assert state.available and not state.devices and not state.error
                xml = bus.call_sync(
                    SERVICE,
                    ROOT_PATH,
                    "org.freedesktop.DBus.Introspectable",
                    "Introspect",
                    None,
                    None,
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
                ).unpack()[0]
                assert DAEMON_IFACE in xml and 'name="devices"' in xml
                print("Real KDE Connect API and empty paired-device snapshot passed")
                initial = len(changes)
                process.terminate()
                process.wait(3)
                wait(lambda: not backend.snapshot().available)
                assert len(changes) > initial
                backend.close()
                assert not backend._subscriptions and not backend._watch
                print("Real daemon disappearance and backend teardown passed")
                bus.close_sync(None)
            except Exception:
                print((root / "daemon.log").read_text()[-6000:])
                raise
            finally:
                if backend is not None:
                    backend.close()
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    main()
