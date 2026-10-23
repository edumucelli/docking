"""Private real-D-Bus sessions for AT-SPI discovery and lifecycle regressions.

Only accessibility objects are fixtures. Session discovery, authentication,
window enumeration, background workers and model publication are production.
Each client process owns separate session/accessibility buses; no host bus is used.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path


class AccessibilitySession:
    def __init__(self, title: str, *, available: bool = True, explicit: bool = False):
        if shutil.which("dbus-daemon") is None:
            raise RuntimeError("dbus-daemon is required for isolated AT-SPI tests")
        self.title = title
        self._temp = tempfile.TemporaryDirectory(prefix="docking-atspi-")
        self.directory = Path(self._temp.name)
        self._log = (self.directory / "worker.log").open("w+")
        env = os.environ.copy()
        for key in (
            "DBUS_SESSION_BUS_ADDRESS",
            "DBUS_STARTER_ADDRESS",
            "DBUS_STARTER_BUS_TYPE",
            "AT_SPI_BUS_ADDRESS",
            "DISPLAY",
            "WAYLAND_DISPLAY",
        ):
            env.pop(key, None)
        env.update(
            NO_AT_BRIDGE="1", GIO_USE_VFS="local", XDG_RUNTIME_DIR=str(self.directory)
        )
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                __name__,
                "--worker",
                str(self.directory),
                title,
                "explicit" if explicit else "available" if available else "missing",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            text=True,
            env=env,
        )
        try:
            self.request("snapshot")
        except BaseException:
            self.close()
            raise

    def request(self, command: str) -> dict:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        ready, _, _ = select.select([self.process.stdout], [], [], 10)
        if not ready:
            raise AssertionError(f"AT-SPI worker timed out: {command}")
        line = self.process.stdout.readline()
        if not line:
            self._log.seek(0)
            raise AssertionError(f"AT-SPI worker exited: {self._log.read()}")
        result = json.loads(line)
        assert "error" not in result, result
        return result

    def wait_for_titles(self, titles: list[str]) -> dict:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = self.request("snapshot")
            if sorted(result["titles"]) == sorted(titles) and result[
                "published"
            ] == len(titles):
                return result
            time.sleep(0.02)
        raise AssertionError(f"Expected {titles}, got {result}")

    def close(self):
        try:
            if self.process.poll() is None:
                try:
                    self.request("exit")
                    self.process.wait(timeout=5)
                except (AssertionError, BrokenPipeError, subprocess.TimeoutExpired):
                    self.process.terminate()
                    self.process.wait(timeout=5)
            for pipe in (self.process.stdin, self.process.stdout):
                if pipe is not None:
                    pipe.close()
        finally:
            self._log.close()
            self._temp.cleanup()


def _worker(directory: Path, title: str, available: bool, explicit: bool) -> None:
    def terminate(_signal, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    with ExitStack() as cleanup:
        _run_worker(directory, title, available, explicit, cleanup)


def _run_worker(directory, title, available, explicit, cleanup):
    # Deliberately no service directories: a missing fixture cannot activate a
    # host-installed accessibility launcher or systemd user service.
    config = directory / "bus.conf"
    config.write_text("""<busconfig><type>session</type><auth>EXTERNAL</auth>
<listen>unix:tmpdir=/tmp</listen>
<policy context="default"><allow own="*"/><allow send_destination="*"/>
<allow receive_sender="*"/></policy></busconfig>""")

    def close_bus(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()

    def bus(name):
        process = subprocess.Popen(
            [
                "dbus-daemon",
                f"--config-file={config}",
                "--nofork",
                "--print-address=1",
                f"--address=unix:path={directory / name}",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        cleanup.callback(close_bus, process)
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], 5)
        assert ready, "Private D-Bus did not start"
        address = process.stdout.readline().strip()
        assert address.startswith(f"unix:path={directory}/"), address
        return process, address

    _session_process, session_address = bus("session")
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = session_address
    from gi.repository import Gio, GLib

    from docking.platform.backends.kwin.atspi_window import AtspiWindowService
    from tests.platform.application_fakes import identity_services

    def connect(address):
        connection = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        connection.set_exit_on_close(False)
        return connection

    class Model:
        published = 0

        def visible_items(self):
            return []

        def update_running(self, *, running):
            self.published = sum(app.count for app in running.values())

    model = Model()
    service = AtspiWindowService(model=model, **identity_services())
    service._REFRESH_INTERVAL_MS = 50
    loop = GLib.MainLoop()
    generation = 0
    access_process = access_connection = None
    access_address = ""

    accessible = Gio.DBusNodeInfo.new_for_xml("""<node>
<interface name="org.a11y.atspi.Accessible">
<property name="Name" type="s" access="read"/>
<method name="GetChildren"><arg direction="out" type="a(so)"/></method>
<method name="GetRoleName"><arg direction="out" type="s"/></method>
<method name="GetInterfaces"><arg direction="out" type="as"/></method>
</interface></node>""").interfaces[0]
    root, frame = "/org/a11y/atspi/accessible/root", "/org/a11y/atspi/accessible/frame"

    def accessible_call(
        connection, sender, path, interface, method, params, invocation
    ):
        if method == "GetChildren":
            children = [(connection.get_unique_name(), frame)] if path == root else []
            reply = GLib.Variant("(a(so))", (children,))
        elif method == "GetRoleName":
            reply = GLib.Variant("(s)", ("application" if path == root else "frame",))
        else:
            reply = GLib.Variant("(as)", ([],))
        invocation.return_value(reply)

    def launch_accessibility():
        nonlocal generation, access_process, access_connection, access_address
        generation += 1
        access_process, access_address = bus(f"access-{generation}")
        access_connection = connect(access_address)
        # The first peer is a real accessible app, not an assumed :1.0 daemon.
        assert access_connection.get_unique_name() == ":1.0"
        for path in (root, frame):
            access_connection.register_object(
                path,
                accessible,
                accessible_call,
                lambda *_: GLib.Variant("s", title),
                None,
            )

    discovery = Gio.DBusNodeInfo.new_for_xml("""<node><interface name="org.a11y.Bus">
<method name="GetAddress"><arg direction="out" type="s"/></method>
</interface></node>""").interfaces[0]
    discovery_connection = connect(session_address)

    def get_address(connection, sender, path, interface, method, params, invocation):
        if available:
            invocation.return_value(GLib.Variant("(s)", (access_address,)))
        else:
            invocation.return_dbus_error("org.a11y.Bus.Unavailable", "Not available")

    discovery_connection.register_object(
        "/org/a11y/bus", discovery, get_address, None, None
    )
    discovery_connection.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "RequestName",
        GLib.Variant("(su)", ("org.a11y.Bus", 0)),
        GLib.VariantType("(u)"),
        Gio.DBusCallFlags.NONE,
        1000,
        None,
    )

    def stop_accessibility():
        if access_process is not None and access_process.poll() is None:
            access_process.terminate()
            access_process.wait(timeout=5)

    def command(channel, condition):
        nonlocal available
        command = sys.stdin.readline().strip()
        try:
            if command == "missing":
                available = False
                stop_accessibility()
            elif command in ("available", "restart"):
                stop_accessibility()
                launch_accessibility()
                available = True
            elif command == "stop":
                service.stop()
            elif command == "start":
                service.start()
            elif command in ("exit", ""):
                loop.quit()
            elif command != "snapshot":
                raise ValueError(command)
            result = {
                "titles": [window.title for window in service.list_all_windows()],
                "published": model.published,
                "address": access_address,
                "running": service._running,
            }
        except Exception as exc:
            result = {"error": str(exc)}
        print(json.dumps(result), flush=True)
        return bool(command and command != "exit")

    try:
        if available or explicit:
            launch_accessibility()
        if explicit:
            os.environ["AT_SPI_BUS_ADDRESS"] = access_address
        service.start()
        GLib.io_add_watch(sys.stdin, GLib.IO_IN | GLib.IO_HUP, command)
        loop.run()
    finally:
        service.stop()
        discovery_connection.close_sync(None)


if __name__ == "__main__":
    assert sys.argv[1] == "--worker"
    _worker(
        Path(sys.argv[2]),
        sys.argv[3],
        sys.argv[4] == "available",
        sys.argv[4] == "explicit",
    )
