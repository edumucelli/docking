"""BSD camera observation, permission recovery, and kernel recording policy."""

import errno
import json
import os
import subprocess
from pathlib import Path

import pytest

from docking.applets.camshield import bsd, helper
from docking.applets.camshield.backend import create_backend
from docking.applets.camshield.state import CameraHolder, CamshieldState, build_tooltip
from docking.platform import environment


@pytest.mark.parametrize("platform", ["freebsd15", "openbsd7"])
def test_camera_probe_uses_kernel_file_table(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(bsd.sys, "platform", platform)
    device = tmp_path / "video0"
    device.touch()
    output = (
        "USER CMD PID FD MOUNT INUM MODE SZ|DV R/W NAME\n"
        f"alice browser 42 3 / 123 crw------- 0 r {device}\n"
        f"alice browser 42 4 / 123 crw------- 0 r {device}\n"
        f"alice idle 51 wd / 123 crw------- 0 r {device}\n"
        f"alice unrelated 52 5 / 123 -rw------- 0 r {tmp_path}/video9\n"
    )
    calls = []

    def read(argv):
        calls.append(argv)
        return output if argv[0] == "fstat" else "1"

    monkeypatch.setattr(bsd, "read_command", read)
    state = bsd.BsdCamshieldBackend().probe(
        dev_root=tmp_path, proc_root=Path("/absent")
    )
    assert state.available and state.active and not state.locked
    assert state.holders == (CameraHolder(42, "browser", (str(device),)),)
    assert calls[0] == ["fstat", str(device)]


def test_failed_file_table_does_not_report_camera_idle(monkeypatch, tmp_path):
    (tmp_path / "video0").touch()
    monkeypatch.setattr(bsd, "read_command", lambda argv: None)
    state = bsd.BsdCamshieldBackend().probe(dev_root=tmp_path, proc_root=tmp_path)
    assert not state.available


def test_unattached_openbsd_device_nodes_are_not_cameras(monkeypatch, tmp_path):
    (tmp_path / "video0").touch()
    monkeypatch.setattr(bsd.sys, "platform", "openbsd7")
    monkeypatch.setattr(
        bsd.os,
        "open",
        lambda *args: (_ for _ in ()).throw(OSError(errno.ENXIO, "No driver")),
    )
    assert not bsd._devices(tmp_path)


@pytest.mark.parametrize("previous", [0, 1])
def test_openbsd_lock_restores_original_policy_after_repeated_locks(
    monkeypatch, tmp_path, previous
):
    monkeypatch.setattr(bsd.sys, "platform", "openbsd7")
    monkeypatch.setattr(bsd, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    current = [previous]
    monkeypatch.setattr(bsd, "read_command", lambda argv: str(current[0]))
    monkeypatch.setattr(
        bsd, "_set_recording", lambda value: current.__setitem__(0, value)
    )
    backend = bsd.BsdCamshieldBackend()
    assert backend.lock() == 0 and current[0] == 0
    assert backend.lock() == 0
    assert backend.unlock() == 0 and current[0] == previous
    assert not (tmp_path / "recording.json").exists()


def test_openbsd_failed_unlock_keeps_recovery_record(monkeypatch, tmp_path):
    monkeypatch.setattr(bsd.sys, "platform", "openbsd7")
    monkeypatch.setattr(bsd, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    record = tmp_path / "recording.json"
    record.write_text(json.dumps({"previous": 1}))
    monkeypatch.setattr(
        bsd,
        "_set_recording",
        lambda value: (_ for _ in ()).throw(OSError("sysctl failed")),
    )
    with pytest.raises(OSError):
        bsd.BsdCamshieldBackend().unlock()
    assert record.exists()


def test_openbsd_unlock_requires_a_valid_recovery_record(monkeypatch, tmp_path):
    monkeypatch.setattr(bsd.sys, "platform", "openbsd7")
    monkeypatch.setattr(bsd, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    for invalid in (None, True, 2, "1"):
        (tmp_path / "recording.json").write_text(json.dumps({"previous": invalid}))
        with pytest.raises(OSError):
            bsd.BsdCamshieldBackend().unlock()


def test_freebsd_lock_refuses_to_destroy_an_unsaved_acl(monkeypatch, tmp_path):
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper.shutil, "which", lambda name: None)
    device = tmp_path / "video0"
    device.touch()
    device.chmod(0o660)
    with pytest.raises(OSError, match="ACL"):
        helper.lock_devices(dev_root=tmp_path, state_dir=tmp_path / "state")
    assert device.stat().st_mode & 0o777 == 0o660


def test_freebsd_device_filesystem_without_acls_uses_mode_recovery(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper, "DEV_ROOT", tmp_path)
    monkeypatch.setattr(helper.shutil, "which", lambda name: None)
    monkeypatch.setattr(bsd, "read_command", lambda argv: "0")
    device = tmp_path / "video0"
    device.touch()
    device.chmod(0o640)
    assert helper.lock_devices(dev_root=tmp_path, state_dir=tmp_path / "state") == 0
    assert helper.unlock_devices(state_dir=tmp_path / "state") == 0
    assert device.stat().st_mode & 0o777 == 0o640


def test_freebsd_failed_acl_restore_retains_recovery_data(monkeypatch, tmp_path):
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper, "DEV_ROOT", tmp_path)
    monkeypatch.setattr(helper, "_restore_acl", lambda **kwargs: False)
    device = tmp_path / "video0"
    device.touch()
    acl = tmp_path / "video0.acl"
    acl.write_text("user::rw-\n")
    helper._write_state(
        state={str(device): helper.DeviceSnapshot(device, 0o660, acl)},
        state_dir=tmp_path,
    )
    assert helper.unlock_devices(state_dir=tmp_path) == 1
    assert str(device) in helper._read_state(state_dir=tmp_path)


def test_camera_factory_and_locked_tooltip(monkeypatch):
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    assert isinstance(create_backend(), bsd.BsdCamshieldBackend)
    assert "Camera locked" in build_tooltip(CamshieldState(True, True, locked=True))


def test_camera_reads_run_off_gtk_and_old_replies_are_ignored(monkeypatch):
    from docking.applets.camshield import applet as camera
    from docking.core.config import Config

    tasks = []
    reads = []

    class Worker:
        def __init__(self, **kwargs):
            pass

        def run_guarded(self, *, key, name, fn, on_result):
            tasks.append((fn, on_result))
            return True

    monkeypatch.setattr(camera, "BackgroundWorker", Worker)
    monkeypatch.setattr(
        camera,
        "probe_camera_state",
        lambda: reads.append(True) or CamshieldState(True, True),
    )
    applet = camera.CamshieldApplet(48, Config())
    assert not reads
    applet.stop()
    read, publish = tasks.pop()
    publish(read())
    assert not applet._state.available


def test_interrupted_repeated_lock_preserves_the_original_recovery_record(
    monkeypatch, tmp_path
):
    from docking.applets.camshield.linux import LinuxCamshieldBackend

    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper, "DEV_ROOT", tmp_path)
    monkeypatch.setattr(helper, "create_backend", LinuxCamshieldBackend)
    monkeypatch.setattr(helper.shutil, "which", lambda name: None)
    device = tmp_path / "video0"
    device.touch()
    device.chmod(0o640)
    state_dir = tmp_path / "state"
    assert helper.lock_devices(dev_root=tmp_path, state_dir=state_dir) == 0
    original = (state_dir / helper.STATE_FILE.name).read_bytes()
    with monkeypatch.context() as failure:
        failure.setattr(
            helper.os,
            "replace",
            lambda *args: (_ for _ in ()).throw(OSError("interrupted")),
        )
        with pytest.raises(OSError):
            helper.lock_devices(dev_root=tmp_path, state_dir=state_dir)
    assert (state_dir / helper.STATE_FILE.name).read_bytes() == original
    assert helper.unlock_devices(state_dir=state_dir) == 0
    assert device.stat().st_mode & 0o777 == 0o640


def test_corrupt_recovery_record_is_not_overwritten_by_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    device = tmp_path / "video0"
    device.touch()
    device.chmod(0o640)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    record = state_dir / helper.STATE_FILE.name
    record.write_text("{")
    with pytest.raises(OSError, match="recovery"):
        helper.lock_devices(dev_root=tmp_path, state_dir=state_dir)
    assert record.read_text() == "{"
    assert device.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize(
    "snapshot",
    [
        {"mode": "0640", "acl_file": None},
        {"mode": True, "acl_file": None},
        {"mode": -1, "acl_file": None},
        {"mode": 0o10000, "acl_file": None},
        {"mode": 0o640},
        {"mode": 0o640, "acl_file": False},
        {"mode": 0o640, "acl_file": "relative"},
        "invalid",
    ],
)
def test_invalid_device_snapshot_cannot_replace_recovery_data(
    monkeypatch, tmp_path, snapshot
):
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper, "DEV_ROOT", tmp_path)
    device = tmp_path / "video0"
    device.touch()
    device.chmod(0)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    record = state_dir / helper.STATE_FILE.name
    original = json.dumps({"version": 1, "devices": {str(device): snapshot}})
    record.write_text(original)
    with pytest.raises(OSError, match="recovery"):
        helper.lock_devices(dev_root=tmp_path, state_dir=state_dir)
    assert record.read_text() == original
    assert device.stat().st_mode & 0o777 == 0


@pytest.mark.skipif(not environment.is_bsd(), reason="requires BSD kernel file tables")
def test_actual_bsd_kernel_reports_open_camera_descriptor(tmp_path):
    device = tmp_path / "video0"
    device.touch()
    backend = bsd.BsdCamshieldBackend()
    with device.open("rb"):
        state = backend.probe(dev_root=tmp_path, proc_root=Path("/nonexistent"))
        assert state.active
        assert any(holder.pid == os.getpid() for holder in state.holders)
    assert not backend.probe(dev_root=tmp_path, proc_root=tmp_path).active


@pytest.mark.skipif(not environment.is_bsd(), reason="requires installed BSD package")
def test_installed_camera_helper_status_is_usable():
    result = subprocess.run(
        ["/usr/local/bin/docking-camshield-helper", "status"],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    assert isinstance(json.loads(result.stdout)["locked"], bool)


@pytest.mark.skipif(
    not bsd.sys.platform.startswith("openbsd")
    or os.environ.get("DOCKING_BSD_VM_TEST") != "1",
    reason="changes kernel recording policy only inside the CI VM",
)
def test_actual_openbsd_lock_and_unlock_restore_kernel_policy(monkeypatch, tmp_path):
    previous = int(bsd.read_command(["/sbin/sysctl", "-n", "kern.video.record"]))
    monkeypatch.setattr(bsd, "_STATE_DIR", tmp_path)
    backend = bsd.BsdCamshieldBackend()
    try:
        bsd._set_recording(1)
        assert backend.lock() == 0
        assert bsd.read_command(["/sbin/sysctl", "-n", "kern.video.record"]) == "0"
        assert backend.lock() == 0
        assert backend.unlock() == 0
        assert bsd.read_command(["/sbin/sysctl", "-n", "kern.video.record"]) == "1"
    finally:
        bsd._set_recording(previous)


@pytest.mark.skipif(
    not bsd.sys.platform.startswith("freebsd"), reason="requires FreeBSD ACL tools"
)
def test_actual_freebsd_permission_and_acl_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(helper, "DEV_ROOT", tmp_path)
    device = tmp_path / "video0"
    device.touch()
    device.chmod(0o640)
    state_dir = tmp_path / "state"
    assert helper.lock_devices(dev_root=tmp_path, state_dir=state_dir) == 0
    assert device.stat().st_mode & 0o777 == 0
    assert helper.unlock_devices(state_dir=state_dir) == 0
    assert device.stat().st_mode & 0o777 == 0o640


@pytest.mark.skipif(
    not bsd.sys.platform.startswith("freebsd"), reason="requires FreeBSD devfs"
)
def test_actual_freebsd_device_filesystem_has_no_extended_acl():
    assert not bsd.BsdCamshieldBackend().requires_acl_backup(Path("/dev/null"))
