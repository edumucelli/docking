"""Tests for Cam Shield applet."""

from __future__ import annotations

from pathlib import Path

import docking.applets.camshield.applet as camshield_applet_mod
from docking.applets.camshield.applet import CamshieldApplet
from docking.applets.camshield.render import render_icon
from docking.applets.camshield.state import (
    CameraHolder,
    CamshieldState,
    build_tooltip,
    holder_label,
    probe_camera_state,
)


def _proc_holder(
    *,
    proc_root: Path,
    pid: int,
    command: str,
    target: Path | str,
) -> None:
    proc = proc_root / str(pid)
    fd = proc / "fd"
    fd.mkdir(parents=True)
    (proc / "comm").write_text(command, encoding="utf-8")
    (fd / "3").symlink_to(target)


class TestProbeCameraState:
    def test_no_video_devices_is_unavailable(self, tmp_path):
        state = probe_camera_state(
            dev_root=tmp_path / "dev",
            proc_root=tmp_path / "proc",
        )

        assert state == CamshieldState(available=False, active=False)

    def test_video_device_without_holder_is_idle(self, tmp_path):
        dev = tmp_path / "dev"
        proc = tmp_path / "proc"
        dev.mkdir()
        proc.mkdir()
        (dev / "video0").touch()

        state = probe_camera_state(dev_root=dev, proc_root=proc)

        assert state.available is True
        assert state.active is False
        assert state.devices == ("video0",)
        assert state.holders == ()

    def test_detects_process_holding_video_fd(self, tmp_path):
        dev = tmp_path / "dev"
        proc = tmp_path / "proc"
        dev.mkdir()
        proc.mkdir()
        video0 = dev / "video0"
        video0.touch()
        _proc_holder(proc_root=proc, pid=123, command="cheese", target=video0)

        state = probe_camera_state(dev_root=dev, proc_root=proc)

        assert state.available is True
        assert state.active is True
        assert state.holders == (
            CameraHolder(pid=123, command="cheese", devices=("video0",)),
        )

    def test_groups_multiple_video_fds_by_process(self, tmp_path):
        dev = tmp_path / "dev"
        proc = tmp_path / "proc"
        dev.mkdir()
        proc.mkdir()
        video0 = dev / "video0"
        video2 = dev / "video2"
        video0.touch()
        video2.touch()

        process = proc / "44"
        fd = process / "fd"
        fd.mkdir(parents=True)
        (process / "comm").write_text("browser", encoding="utf-8")
        (fd / "3").symlink_to(video2)
        (fd / "4").symlink_to(video0)

        state = probe_camera_state(dev_root=dev, proc_root=proc)

        assert state.holders == (
            CameraHolder(pid=44, command="browser", devices=("video0", "video2")),
        )

    def test_ignores_non_camera_fds_and_non_pid_dirs(self, tmp_path):
        dev = tmp_path / "dev"
        proc = tmp_path / "proc"
        dev.mkdir()
        proc.mkdir()
        (dev / "video0").touch()
        other = dev / "audio0"
        other.touch()
        _proc_holder(proc_root=proc, pid=7, command="audio", target=other)
        (proc / "self").mkdir()

        state = probe_camera_state(dev_root=dev, proc_root=proc)

        assert state.active is False
        assert state.holders == ()

    def test_ignores_non_device_path_with_same_video_name(self, tmp_path):
        dev = tmp_path / "dev"
        proc = tmp_path / "proc"
        other = tmp_path / "other"
        dev.mkdir()
        proc.mkdir()
        other.mkdir()
        (dev / "video0").touch()
        fake_video = other / "video0"
        fake_video.touch()
        _proc_holder(proc_root=proc, pid=9, command="viewer", target=fake_video)

        state = probe_camera_state(dev_root=dev, proc_root=proc)

        assert state.active is False
        assert state.holders == ()


class TestLabels:
    def test_tooltip_for_active_camera_lists_holders(self):
        state = CamshieldState(
            available=True,
            active=True,
            devices=("video0",),
            holders=(CameraHolder(pid=1, command="app", devices=("video0",)),),
        )

        text = build_tooltip(state)

        assert "Camera active" in text
        assert "app (PID 1) using video0" in text

    def test_holder_label(self):
        label = holder_label(
            CameraHolder(pid=42, command="browser", devices=("video0",))
        )

        assert label == "browser (PID 42) - video0"


class TestRender:
    def test_render_icon(self):
        assert render_icon(size=48, available=True, active=True) is not None
        assert (
            render_icon(size=48, available=True, active=True, pulse_phase=0.5)
            is not None
        )
        assert render_icon(size=48, available=False, active=False) is not None


class TestApplet:
    def test_applet_presents_state(self, monkeypatch):
        state = CamshieldState(
            available=True,
            active=True,
            devices=("video0",),
            holders=(CameraHolder(pid=7, command="camera-app", devices=("video0",)),),
        )
        monkeypatch.setattr(camshield_applet_mod, "probe_camera_state", lambda: state)

        applet = CamshieldApplet(icon_size=48)

        assert applet.item.desktop_id == "applet://camshield"
        assert "Camera active" in applet.item.name
        assert applet.item.icon is not None

    def test_menu_contains_refresh(self, monkeypatch):
        state = CamshieldState(available=True, active=False, devices=("video0",))
        monkeypatch.setattr(camshield_applet_mod, "probe_camera_state", lambda: state)
        applet = CamshieldApplet(icon_size=48)

        labels = [item.get_label() for item in applet.get_menu_items()]

        assert "Camera idle" in labels
        assert "Refresh Now" in labels

    def test_pulse_tick_repaints_icon(self, monkeypatch):
        state = CamshieldState(
            available=True,
            active=True,
            devices=("video0",),
            holders=(CameraHolder(pid=7, command="camera-app", devices=("video0",)),),
        )
        monkeypatch.setattr(camshield_applet_mod, "probe_camera_state", lambda: state)
        applet = CamshieldApplet(icon_size=48)
        notifications = 0

        def notify():
            nonlocal notifications
            notifications += 1

        applet.start(notify=notify)

        assert applet._pulse_tick() is True
        assert applet._pulse_phase > 0.0
        assert applet.item.icon is not None
        assert notifications == 1
        applet.stop()
