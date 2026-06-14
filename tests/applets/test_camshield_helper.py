"""Tests for the privileged Cam Shield helper logic."""

from __future__ import annotations

import json
import stat
import subprocess

import docking.applets.camshield.helper as helper


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_discover_video_devices_filters_names(tmp_path):
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "video0").touch()
    (dev / "video10").touch()
    (dev / "video").touch()
    (dev / "videoA").touch()

    devices = helper.discover_video_devices(dev_root=dev)

    assert devices == (dev / "video0", dev / "video10")


def test_discover_video_devices_missing_root_returns_empty(tmp_path):
    assert helper.discover_video_devices(dev_root=tmp_path / "missing") == ()


def test_lock_without_devices_reports_error(tmp_path, monkeypatch, capsys):
    dev = tmp_path / "dev"
    dev.mkdir()
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)

    assert helper.lock_devices(dev_root=dev, state_dir=tmp_path / "state") == 1
    assert "No camera devices" in capsys.readouterr().err


def test_lock_and_unlock_restore_previous_mode(tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    state_dir = tmp_path / "state"
    dev.mkdir()
    video0 = dev / "video0"
    video0.touch()
    video0.chmod(0o660)

    monkeypatch.setattr(helper, "DEV_ROOT", dev)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper.shutil, "which", lambda _cmd: None)

    assert helper.lock_devices(dev_root=dev, state_dir=state_dir) == 0
    assert _mode(video0) == 0

    assert helper.unlock_devices(state_dir=state_dir) == 0
    assert _mode(video0) == 0o660
    assert not (state_dir / helper.STATE_FILE.name).exists()


def test_unlock_without_state_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)

    assert helper.unlock_devices(state_dir=tmp_path / "state") == 1
    assert "No locked camera" in capsys.readouterr().err


def test_lock_preserves_original_snapshot_on_repeated_lock(tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    state_dir = tmp_path / "state"
    dev.mkdir()
    video0 = dev / "video0"
    video0.touch()
    video0.chmod(0o660)

    monkeypatch.setattr(helper, "DEV_ROOT", dev)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper.shutil, "which", lambda _cmd: None)

    assert helper.lock_devices(dev_root=dev, state_dir=state_dir) == 0
    assert helper.lock_devices(dev_root=dev, state_dir=state_dir) == 0
    assert helper.unlock_devices(state_dir=state_dir) == 0

    assert _mode(video0) == 0o660


def test_unlock_skips_missing_and_disallowed_devices(tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    state_dir = tmp_path / "state"
    dev.mkdir()
    state_dir.mkdir()
    video0 = dev / "video0"
    video0.touch()
    video0.chmod(0)
    monkeypatch.setattr(helper, "DEV_ROOT", dev)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    (state_dir / helper.STATE_FILE.name).write_text(
        json.dumps(
            {
                "version": 1,
                "devices": {
                    str(video0): {"mode": 0o660, "acl_file": None},
                    str(tmp_path / "video9"): {"mode": 0o660, "acl_file": None},
                    str(dev / "missing"): {"mode": 0o660, "acl_file": None},
                },
            }
        ),
        encoding="utf-8",
    )

    assert helper.unlock_devices(state_dir=state_dir) == 0
    assert _mode(video0) == 0o660


def test_status_reports_locked_devices(tmp_path, monkeypatch, capsys):
    dev = tmp_path / "dev"
    state_dir = tmp_path / "state"
    dev.mkdir()
    video0 = dev / "video0"
    video0.touch()
    video0.chmod(0)

    monkeypatch.setattr(helper, "DEV_ROOT", dev)

    assert helper.status_devices(dev_root=dev, state_dir=state_dir) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] is True
    assert payload["locked"] is True
    assert payload["devices"] == [
        {
            "path": str(video0),
            "mode": "0000",
            "locked": True,
            "recorded": False,
        }
    ]


def test_status_marks_recorded_devices(tmp_path, monkeypatch, capsys):
    dev = tmp_path / "dev"
    state_dir = tmp_path / "state"
    dev.mkdir()
    state_dir.mkdir()
    video0 = dev / "video0"
    video0.touch()
    video0.chmod(0o660)
    monkeypatch.setattr(helper, "DEV_ROOT", dev)
    helper._write_state(
        state={str(video0): helper.DeviceSnapshot(path=video0, mode=0o660)},
        state_dir=state_dir,
    )

    assert helper.status_devices(dev_root=dev, state_dir=state_dir) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["devices"][0]["recorded"] is True
    assert payload["devices"][0]["locked"] is False


def test_main_rejects_lock_without_root(monkeypatch, capsys):
    monkeypatch.setattr(helper.os, "geteuid", lambda: 1000)

    assert helper.main(["lock"]) == 77
    assert "must be run as root" in capsys.readouterr().err


def test_acl_snapshot_save_and_restore_success(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    device = tmp_path / "video0"
    device.touch()
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        calls.append(tuple(argv))
        stdout = kwargs.get("stdout")
        if stdout is not None and hasattr(stdout, "write"):
            stdout.write("# acl\n")
        return subprocess.CompletedProcess(args=argv, returncode=0)

    monkeypatch.setattr(
        helper.shutil,
        "which",
        lambda command: f"/usr/bin/{command}",
    )
    monkeypatch.setattr(helper.subprocess, "run", fake_run)

    acl_file = helper._save_acl_snapshot(device=device, state_dir=state_dir)
    assert acl_file is not None
    assert acl_file.exists()
    assert helper._restore_acl(
        snapshot=helper.DeviceSnapshot(path=device, mode=0o660, acl_file=acl_file)
    )
    assert calls[0][0].endswith("getfacl")
    assert calls[1][0].endswith("setfacl")


def test_acl_snapshot_failures_return_false_or_none(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    device = tmp_path / "video0"
    device.touch()

    monkeypatch.setattr(helper.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        helper.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, args[0])
        ),
    )
    assert helper._save_acl_snapshot(device=device, state_dir=state_dir) is None

    acl_file = state_dir / "missing.acl"
    snapshot = helper.DeviceSnapshot(path=device, mode=0o660, acl_file=acl_file)
    assert helper._restore_acl(snapshot=snapshot) is False

    acl_file.write_text("# acl\n", encoding="utf-8")
    monkeypatch.setattr(helper.shutil, "which", lambda _command: None)
    assert helper._restore_acl(snapshot=snapshot) is False


def test_read_state_filters_malformed_entries(tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    state_dir = tmp_path / "state"
    dev.mkdir()
    state_dir.mkdir()
    video0 = dev / "video0"
    monkeypatch.setattr(helper, "DEV_ROOT", dev)
    (state_dir / helper.STATE_FILE.name).write_text(
        json.dumps(
            {
                "version": 1,
                "devices": {
                    str(video0): {"mode": 0o660, "acl_file": str(tmp_path / "a")},
                    "relative": {"mode": 0o660},
                    str(dev / "videoX"): {"mode": 0o660},
                    str(dev / "video1"): {"mode": "bad"},
                    str(dev / "video2"): "bad",
                },
            }
        ),
        encoding="utf-8",
    )

    state = helper._read_state(state_dir=state_dir)

    assert state == {
        str(video0): helper.DeviceSnapshot(
            path=video0,
            mode=0o660,
            acl_file=tmp_path / "a",
        )
    }


def test_read_state_rejects_bad_json_version_and_shape(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    path = state_dir / helper.STATE_FILE.name

    path.write_text("{", encoding="utf-8")
    assert helper._read_state(state_dir=state_dir) == {}

    path.write_text(json.dumps({"version": 999, "devices": {}}), encoding="utf-8")
    assert helper._read_state(state_dir=state_dir) == {}

    path.write_text(json.dumps({"version": 1, "devices": []}), encoding="utf-8")
    assert helper._read_state(state_dir=state_dir) == {}


def test_device_locked_handles_stat_error(tmp_path):
    assert helper._device_locked(tmp_path / "missing") is False


def test_main_dispatches_commands_and_os_errors(monkeypatch, capsys):
    calls: list[str] = []
    monkeypatch.setattr(helper, "lock_devices", lambda: calls.append("lock") or 0)
    monkeypatch.setattr(helper, "unlock_devices", lambda: calls.append("unlock") or 0)
    monkeypatch.setattr(helper, "status_devices", lambda: calls.append("status") or 0)

    assert helper.main(["lock"]) == 0
    assert helper.main(["unlock"]) == 0
    assert helper.main(["status"]) == 0
    assert calls == ["lock", "unlock", "status"]

    monkeypatch.setattr(
        helper,
        "status_devices",
        lambda: (_ for _ in ()).throw(OSError("boom")),
    )
    assert helper.main(["status"]) == 1
    assert "Camera helper failed" in capsys.readouterr().err
