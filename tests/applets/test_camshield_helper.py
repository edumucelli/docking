"""Tests for the privileged Cam Shield helper logic."""

from __future__ import annotations

import json
import stat

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


def test_main_rejects_lock_without_root(monkeypatch, capsys):
    monkeypatch.setattr(helper.os, "geteuid", lambda: 1000)

    assert helper.main(["lock"]) == 77
    assert "must be run as root" in capsys.readouterr().err
