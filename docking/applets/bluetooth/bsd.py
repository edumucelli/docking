"""FreeBSD Bluetooth controllers, discovery, and native pairing."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from docking.applets.bluetooth.backend import BluetoothBackend, BluetoothCapabilities
from docking.applets.bluetooth.state import (
    BluetoothAdapterState,
    BluetoothDeviceState,
    BluetoothState,
    _device_sort_key,
    adapter_from_state,
    unavailable_state,
)
from docking.log import get_logger

log = get_logger("bluetooth.bsd")
_ADDRESS = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}")
_NODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,24}hci")
_HCCONTROL = "/usr/sbin/hccontrol"


def _run(argv: list[str], *, timeout: float = 2) -> str | None:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("Bluetooth command failed: %s: %s", argv[0], exc)
        return None


def _device_path(node: str, address: str) -> str:
    return f"{node}/{address.lower()}"


def _parse_device(path: str) -> tuple[str, str] | None:
    node, separator, address = path.partition("/")
    if separator and _NODE.fullmatch(node) and _ADDRESS.fullmatch(address):
        return node, address.lower()
    return None


def _connections(output: str) -> dict[str, str]:
    """Keep only open ACL connections, with their controller-local handles."""
    result = {}
    for line in output.splitlines():
        fields = line.split()
        if (
            len(fields) == 9
            and _ADDRESS.fullmatch(fields[0])
            and fields[1].isdigit()
            and fields[2] == "ACL"
            and fields[-1] == "OPEN"
        ):
            result[fields[0].lower()] = fields[1]
    return result


class BsdBluetoothBackend(BluetoothBackend):
    # FreeBSD has no BlueZ Trusted property. Removing a pairing also requires
    # coordinating hcsecd's live key cache and configuration, not just deleting
    # a controller key. Those operations remain with the system administrator.
    capabilities = BluetoothCapabilities(trust=False, remove_pairing=False)

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._adapters: dict[str, BluetoothAdapterState] = {}
        self._discovered: dict[str, set[str]] = {}
        self._scans: dict[str, subprocess.Popen[str]] = {}
        self._paired_file = (
            Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
            / "docking/bluetooth.json"
        )
        self._paired: set[str] = set()
        try:
            data = json.loads(self._paired_file.read_text())
            if isinstance(data, list):
                self._paired = {
                    path
                    for path in data
                    if isinstance(path, str) and _parse_device(path)
                }
        except (OSError, ValueError):
            pass

    def _hci(self, node: str, command: str, *args: str) -> str | None:
        return _run([_HCCONTROL, "-N", "-n", node, command, *args])

    def get_state(self, active_adapter_path: str | None = None) -> BluetoothState:
        if sys.platform.startswith("openbsd"):
            return unavailable_state("OpenBSD has no Bluetooth stack")
        listing = _run([_HCCONTROL, "read_node_list"])
        if listing is None:
            return unavailable_state("FreeBSD Bluetooth controllers unavailable")
        nodes = {
            fields[0]
            for line in listing.splitlines()
            if (fields := line.split()) and _NODE.fullmatch(fields[0])
        }
        # USB controllers remain discoverable after their HCI stack is stopped.
        usb = _run(["/sbin/sysctl", "-a", "-N", "dev.ubt"]) or ""
        usb_nodes = {
            f"ubt{number}hci" for number in re.findall(r"dev\.ubt\.(\d+)\.", usb)
        }
        with self._lock:
            known = set(self._adapters) | nodes | usb_nodes
            paired = set(self._paired)
            discovered = {node: set(items) for node, items in self._discovered.items()}
            scanning = set(self._scans)
        names: dict[str, str] = {}
        try:
            for line in Path("/etc/bluetooth/hosts").read_text().splitlines():
                fields = line.split("#", 1)[0].split()
                if len(fields) >= 2 and _ADDRESS.fullmatch(fields[0]):
                    names[fields[0].lower()] = fields[1]
        except OSError:
            pass
        adapters = []
        devices = []
        for node in sorted(known):
            powered = node in nodes
            local_name = self._hci(node, "read_local_name") if powered else None
            name = (local_name or "").partition(":")[2].strip() or node
            local_addr = self._hci(node, "read_node_bdaddr") if powered else None
            match = _ADDRESS.search(local_addr or "")
            adapters.append(
                BluetoothAdapterState(
                    node,
                    name,
                    name,
                    powered,
                    node in scanning,
                    match[0].lower() if match else "",
                )
            )
            connections = (
                _connections(self._hci(node, "read_connection_list") or "")
                if powered
                else {}
            )
            addresses = discovered.get(node, set()) | set(connections)
            addresses.update(
                path.split("/", 1)[1] for path in paired if path.startswith(node + "/")
            )
            for address in sorted(addresses):
                path = _device_path(node, address)
                label = names.get(address, address)
                devices.append(
                    BluetoothDeviceState(
                        path,
                        node,
                        label,
                        label,
                        address,
                        "bluetooth",
                        path in paired,
                        False,
                        address in connections,
                        None,
                        None,
                    )
                )
        with self._lock:
            self._adapters = {adapter.path: adapter for adapter in adapters}
        state = BluetoothState(
            bool(adapters),
            tuple(adapters),
            tuple(sorted(devices, key=_device_sort_key)),
            "",
        )
        selected = adapter_from_state(state, active_adapter_path) or ""
        return BluetoothState(state.available, state.adapters, state.devices, selected)

    def _privileged(self, argv: list[str], *, timeout: float = 120) -> bool:
        pkexec = shutil.which("pkexec")
        return bool(pkexec) and _run([pkexec, *argv], timeout=timeout) is not None

    def set_adapter_power(self, adapter_path: str, powered: bool) -> bool:
        if not _NODE.fullmatch(adapter_path):
            return False
        if not powered:
            self.stop_discovery(adapter_path)
        return self._privileged(
            [
                "/usr/sbin/service",
                "bluetooth",
                "onestart" if powered else "onestop",
                adapter_path.removesuffix("hci"),
            ]
        )

    def start_discovery(self, adapter_path: str) -> bool:
        if not _NODE.fullmatch(adapter_path):
            return False
        with self._lock:
            if adapter_path in self._scans:
                return True
            try:
                child = subprocess.Popen(
                    [_HCCONTROL, "-N", "-n", adapter_path, "inquiry"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    env={**os.environ, "LC_ALL": "C"},
                )
            except OSError:
                return False
            self._scans[adapter_path] = child
        try:
            output, _ = child.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            output = ""
        with self._lock:
            if self._scans.get(adapter_path) is not child:
                return False
            del self._scans[adapter_path]
            if child.returncode != 0:
                return False
            self._discovered[adapter_path] = {
                address.lower() for address in _ADDRESS.findall(output)
            }
        return True

    def stop_discovery(self, adapter_path: str, quiet: bool = False) -> bool:
        with self._lock:
            child = self._scans.pop(adapter_path, None)
            if child is not None and child.poll() is None:
                child.terminate()
        # Never send inquiry_cancel: another client may own the inquiry.
        return True

    def close(self) -> None:
        with self._lock:
            for node in tuple(self._scans):
                self.stop_discovery(node)

    def connect_device(self, device_path: str) -> bool:
        parsed = _parse_device(device_path)
        if parsed is None:
            return False
        node, address = parsed
        if address in _connections(self._hci(node, "read_connection_list") or ""):
            return True
        return self._privileged(
            [_HCCONTROL, "-N", "-n", node, "create_connection", address]
        )

    def disconnect_device(self, device_path: str) -> bool:
        parsed = _parse_device(device_path)
        if parsed is None:
            return False
        node, address = parsed
        handle = _connections(self._hci(node, "read_connection_list") or "").get(
            address
        )
        if handle is None:
            return True
        return self._privileged([_HCCONTROL, "-N", "-n", node, "disconnect", handle])

    def pair_device(
        self, device_path: str, address: str = "", timeout_s: int = 20
    ) -> bool:
        parsed = _parse_device(device_path)
        if parsed is None or (address and address.lower() != parsed[1]):
            return False
        terminal, pkexec = shutil.which("xterm"), shutil.which("pkexec")
        if terminal is None or pkexec is None:
            return False
        node, address = parsed
        # The OS owns PIN entry, hcsecd, and HID setup. Keep the terminal alive
        # until its interactive operation completes; an xterm launch is not a pair.
        with tempfile.TemporaryDirectory(prefix="docking-bluetooth-") as directory:
            status = Path(directory) / "status"
            argv = [
                pkexec,
                "/usr/sbin/bluetooth-config",
                "scan",
                "-n",
                node,
                "-d",
                address,
            ]
            command = (
                shlex.join(argv)
                + '; result=$?; printf "%s\\n" "$result" > '
                + shlex.quote(str(status))
                + '; exit "$result"'
            )
            result = _run(
                [terminal, "-T", "Bluetooth pairing", "-e", "/bin/sh", "-c", command],
                timeout=max(timeout_s, 300),
            )
            try:
                if result is None or status.read_text().strip() != "0":
                    return False
            except OSError:
                return False
        with self._lock:
            self._paired.add(device_path)
            try:
                self._paired_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                temporary = self._paired_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(sorted(self._paired)))
                temporary.replace(self._paired_file)
            except OSError as exc:
                log.debug("Could not remember configured Bluetooth device: %s", exc)
        return True

    def remove_device(self, adapter_path: str, device_path: str) -> bool:
        return False

    def set_trusted(self, device_path: str, trusted: bool) -> bool:
        return False
