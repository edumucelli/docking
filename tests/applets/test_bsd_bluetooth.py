"""FreeBSD controller command parsing, discovery ownership, and pairing results."""

import shlex
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from docking.applets.bluetooth.implementations.bsd import backend as bsd
from docking.platform.composition.bsd import BsdAppletBackendFactories

create_backend = BsdAppletBackendFactories().bluetooth
from docking.platform import environment

NODE = "ubt0hci"
ADDRESS = "00:11:22:33:44:55"
DEVICE = f"{NODE}/{ADDRESS}"
CONNECTIONS = (
    "Remote BD_ADDR Handle Type Mode Role Encrypt Pending Queue State\n"
    f"{ADDRESS} 41 ACL 0 MAST NONE 0 0 OPEN\n"
    "66:77:88:99:aa:bb 42 SCO 0 MAST NONE 0 0 OPEN\n"
    "aa:bb:cc:dd:ee:ff 43 ACL 0 MAST NONE 0 0 WAIT_CONNECT\n"
)


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(bsd.sys, "platform", "freebsd15")
    return bsd.BsdBluetoothBackend()


def test_controller_discovery_and_open_acl_connections(backend, monkeypatch):
    def run(argv, **kwargs):
        if argv[-1] == "read_node_list":
            return "Name ID Num hooks\nubt0hci 0000000a 3\n"
        if "dev.ubt" in argv:
            return "dev.ubt.0.%desc\ndev.ubt.1.%desc"
        return {
            "read_local_name": "Local name: Desktop",
            "read_node_bd_addr": "BD_ADDR: aa:bb:cc:dd:ee:ff",
            "read_connection_list": CONNECTIONS,
        }.get(argv[-1])

    monkeypatch.setattr(bsd, "_run", run)
    backend._paired.add(DEVICE)
    state = backend.get_state()
    assert state.available and state.active_adapter_path == NODE
    assert state.adapters[0].address == "aa:bb:cc:dd:ee:ff"
    assert [(a.path, a.powered) for a in state.adapters] == [
        (NODE, True),
        ("ubt1hci", False),
    ]
    assert state.devices[0].address == ADDRESS
    assert state.devices[0].paired and state.devices[0].connected
    assert bsd._connections(CONNECTIONS) == {ADDRESS: "41"}


def test_missing_controller_and_openbsd_are_unavailable(backend, monkeypatch):
    monkeypatch.setattr(bsd, "_run", lambda argv, **kwargs: None)
    assert not backend.get_state().available
    monkeypatch.setattr(bsd.sys, "platform", "openbsd7")
    assert "OpenBSD" in backend.get_state().error


def test_factory_selects_bsd_without_importing_bluez(monkeypatch):
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    monkeypatch.setitem(
        bsd.sys.modules, "docking.applets.bluetooth.implementations.linux.backend", None
    )
    assert isinstance(create_backend(), bsd.BsdBluetoothBackend)


@pytest.mark.parametrize(
    "path", ["-x/00:11:22:33:44:55", "ubt0hci/$(id)", "../x", "ubt0hci/not-a-mac"]
)
def test_malformed_identifiers_cannot_reach_privileged_tools(
    backend, monkeypatch, path
):
    calls = []
    monkeypatch.setattr(bsd, "_run", lambda argv, **kwargs: calls.append(argv))
    assert not backend.connect_device(path)
    assert not backend.disconnect_device(path)
    assert not backend.pair_device(path)
    assert not calls


def test_power_and_connections_use_validated_native_commands(backend, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return CONNECTIONS if argv[-1] == "read_connection_list" else ""

    monkeypatch.setattr(bsd, "_run", run)
    monkeypatch.setattr(bsd.shutil, "which", lambda command: "/usr/local/bin/pkexec")
    assert backend.set_adapter_power(NODE, False)
    assert calls[-1] == [
        "/usr/local/bin/pkexec",
        "/usr/sbin/service",
        "bluetooth",
        "onestop",
        "ubt0",
    ]
    assert backend.connect_device(DEVICE)  # already connected; no new privileged action
    assert backend.disconnect_device(DEVICE)
    assert calls[-1][-2:] == ["disconnect", "41"]


def test_discovery_updates_only_its_own_scan(backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class Scan:
        returncode = 0

        def __init__(self, argv, **kwargs):
            self.argv = argv

        def communicate(self, timeout=None):
            entered.set()
            assert release.wait(3)
            return f"BD_ADDR: {ADDRESS}", ""

        def poll(self):
            return None

        def terminate(self):
            self.returncode = -15
            release.set()

    monkeypatch.setattr(bsd.subprocess, "Popen", Scan)
    results = []
    thread = threading.Thread(
        target=lambda: results.append(backend.start_discovery(NODE))
    )
    thread.start()
    assert entered.wait(3)
    backend.close()
    thread.join(3)
    assert not thread.is_alive()
    assert results == [False]
    assert not backend._discovered
    assert not backend._scans


def test_pending_scan_cannot_start_after_backend_close(backend, monkeypatch):
    monkeypatch.setattr(
        bsd.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("scan started after close"),
    )
    backend.close()
    assert not backend.start_discovery(NODE)


@pytest.mark.parametrize("exit_status", ["0", "1", None])
def test_pairing_requires_native_config_completion(backend, monkeypatch, exit_status):
    monkeypatch.setattr(
        bsd.shutil, "which", lambda command: "/usr/local/bin/" + command
    )

    def run(argv, **kwargs):
        script = argv[-1]
        assert "bluetooth-config scan -n ubt0hci -d " + ADDRESS in script
        # The terminal's exit status alone must not be interpreted as pairing.
        if exit_status is not None:
            status_path = shlex.split(script.split(" > ", 1)[1].split(";", 1)[0])[0]
            Path(status_path).write_text(exit_status)
        return ""

    monkeypatch.setattr(bsd, "_run", run)
    assert backend.pair_device(DEVICE, ADDRESS) is (exit_status == "0")
    assert (DEVICE in backend._paired) is (exit_status == "0")
    if exit_status == "0":
        assert DEVICE in bsd.BsdBluetoothBackend()._paired


def test_failed_and_timed_out_native_commands_are_failures(monkeypatch):
    monkeypatch.setattr(
        bsd.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    assert bsd._run(["hccontrol"]) is None
    monkeypatch.setattr(
        bsd.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(args[0], 2)
        ),
    )
    assert bsd._run(["hccontrol"]) is None


@pytest.mark.skipif(
    not bsd.sys.platform.startswith("freebsd"), reason="requires FreeBSD"
)
def test_actual_freebsd_controller_query_without_hardware():
    state = bsd.BsdBluetoothBackend().get_state()
    assert state.available == bool(state.adapters)
    assert state.devices == () or state.available
