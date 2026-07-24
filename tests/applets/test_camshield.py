"""Tests for Cam Shield applet."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.camshield.applet as camshield_applet_mod
import docking.applets.camshield.state as camshield_state_mod
from docking.applets.camshield.applet import CamshieldApplet
from docking.applets.camshield.render import render_icon
from docking.applets.camshield.state import (
    CameraHolder,
    CamshieldState,
    build_tooltip,
    holder_label,
    probe_camera_state,
)
from docking.core.config import Config


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

    def test_deleted_fd_target_still_matches_device(self, tmp_path):
        dev = tmp_path / "dev"
        proc = tmp_path / "proc"
        dev.mkdir()
        proc.mkdir()
        video0 = dev / "video0"
        video0.touch()

        class _Fd:
            def readlink(self):
                return f"{video0} (deleted)"

        assert (
            camshield_state_mod._fd_video_device_name(
                _Fd(),
                {"video0": video0.resolve(strict=False)},
            )
            == "video0"
        )

    def test_process_command_falls_back_to_cmdline_and_unknown(self, tmp_path):
        proc = tmp_path / "123"
        proc.mkdir()
        (proc / "cmdline").write_text("/usr/bin/browser\0--camera", encoding="utf-8")

        assert camshield_state_mod._process_command(proc) == "browser"

        empty_proc = tmp_path / "124"
        empty_proc.mkdir()
        assert camshield_state_mod._process_command(empty_proc) == "Unknown"

    def test_probe_handles_unreadable_roots(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            Path,
            "glob",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bad dev")),
        )

        assert probe_camera_state(dev_root=tmp_path, proc_root=tmp_path) == (
            CamshieldState(False, False)
        )


class TestLabels:
    def test_tooltip_unavailable_idle_and_more_holders(self):
        assert "No camera devices found" in build_tooltip(CamshieldState(False, False))
        assert "Camera idle" in build_tooltip(CamshieldState(True, False))
        holders = tuple(
            CameraHolder(pid=i, command=f"app{i}", devices=("video0",))
            for i in range(8)
        )
        text = build_tooltip(CamshieldState(True, True, ("video0",), holders))

        assert "2 more" in text

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

        applet = CamshieldApplet(icon_size=48, config=Config())

        assert applet.item.desktop_id == "applet://camshield"
        assert "Camera active" in applet.item.name
        assert applet.item.icon is not None

    def test_menu_contains_refresh(self, monkeypatch):
        state = CamshieldState(available=True, active=False, devices=("video0",))
        monkeypatch.setattr(camshield_applet_mod, "probe_camera_state", lambda: state)
        applet = CamshieldApplet(icon_size=48, config=Config())

        labels = [item.get_label() for item in applet.get_menu_items()]

        assert "Camera idle" in labels
        assert "Refresh Now" in labels

    def test_menu_unavailable_disables_helper_actions(self, monkeypatch):
        state = CamshieldState(available=False, active=False)
        monkeypatch.setattr(camshield_applet_mod, "probe_camera_state", lambda: state)
        monkeypatch.setattr(
            camshield_applet_mod.CamshieldApplet,
            "_helper_available",
            lambda _self: False,
        )
        applet = CamshieldApplet(icon_size=48, config=Config())

        items = applet.get_menu_items()
        labels = [item.get_label() for item in items]

        assert "No camera devices found" in labels
        assert "Camera lock helper unavailable" in labels
        assert not next(
            item for item in items if item.get_label() == "Lock Camera"
        ).get_sensitive()

    def test_start_stop_refresh_and_tick(self, monkeypatch):
        state = CamshieldState(available=True, active=False, devices=("video0",))
        monkeypatch.setattr(camshield_applet_mod, "probe_camera_state", lambda: state)
        idle = MagicMock()
        add_seconds = MagicMock(return_value=11)
        removed: list[int] = []
        monkeypatch.setattr(camshield_applet_mod.GLib, "idle_add", idle)
        monkeypatch.setattr(
            camshield_applet_mod.GLib,
            "timeout_add_seconds",
            add_seconds,
        )
        monkeypatch.setattr(
            camshield_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )
        applet = CamshieldApplet(icon_size=48, config=Config())

        applet.start(lambda: None)
        assert applet._timer_id == 11
        assert idle.call_count == 1

        assert applet._refresh_once() is False
        assert applet._tick() is True

        applet._pulse_timer_id = 42
        applet.stop()

        assert removed == [11, 42]
        assert applet._timer_id == 0
        assert applet._pulse_timer_id == 0

    def test_pulse_tick_repaints_icon(self, monkeypatch):
        state = CamshieldState(
            available=True,
            active=True,
            devices=("video0",),
            holders=(CameraHolder(pid=7, command="camera-app", devices=("video0",)),),
        )
        monkeypatch.setattr(camshield_applet_mod, "probe_camera_state", lambda: state)
        applet = CamshieldApplet(icon_size=48, config=Config())
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

    def test_pulse_timer_stops_when_state_becomes_idle(self, monkeypatch):
        states = iter(
            [
                CamshieldState(True, True, ("video0",)),
                CamshieldState(True, False, ("video0",)),
            ]
        )
        monkeypatch.setattr(
            camshield_applet_mod, "probe_camera_state", lambda: next(states)
        )
        removed: list[int] = []
        monkeypatch.setattr(
            camshield_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )
        applet = CamshieldApplet(icon_size=48, config=Config())
        applet._pulse_timer_id = 42
        applet._pulse_phase = 0.5

        applet._refresh_now()

        assert removed == [42]
        assert applet._pulse_timer_id == 0
        assert applet._pulse_phase == 0.0

    def test_helper_available_requires_pkexec_and_helper(self, monkeypatch, tmp_path):
        helper_path = tmp_path / "helper.py"
        monkeypatch.setattr(camshield_applet_mod, "_SOURCE_HELPER", helper_path)
        monkeypatch.setattr(
            camshield_applet_mod.shutil,
            "which",
            lambda command: "/usr/bin/pkexec" if command == "pkexec" else None,
        )
        applet = CamshieldApplet(icon_size=48, config=Config())

        assert applet._helper_available() is False

        helper_path.touch()
        assert applet._helper_available() is True

    def test_run_helper_action_and_command_paths(self, monkeypatch):
        applet = CamshieldApplet(icon_size=48, config=Config())
        applet._run_helper_command = MagicMock()

        class _Thread:
            def __init__(self, *, target, args, daemon):
                self.target = target
                self.args = args
                self.daemon = daemon

            def start(self):
                self.target(*self.args)

        monkeypatch.setattr(
            camshield_applet_mod, "_helper_command", lambda **_: ["cmd"]
        )
        monkeypatch.setattr(camshield_applet_mod.threading, "Thread", _Thread)

        applet._run_helper_action("lock")
        applet._run_helper_command.assert_called_once_with(["cmd"])

        monkeypatch.setattr(camshield_applet_mod, "_helper_command", lambda **_: None)
        applet._run_helper_action("lock")

    def test_run_helper_command_refreshes_and_logs_failures(self, monkeypatch):
        applet = CamshieldApplet(icon_size=48, config=Config())
        idle: list[object] = []
        monkeypatch.setattr(
            camshield_applet_mod.GLib, "idle_add", lambda cb: idle.append(cb)
        )
        monkeypatch.setattr(
            camshield_applet_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
        )

        applet._run_helper_command(["cmd"])

        assert idle == [applet._refresh_once]

        monkeypatch.setattr(
            camshield_applet_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")),
        )
        applet._run_helper_command(["cmd"])

    def test_helper_command_prefers_installed_helper(self, monkeypatch):
        paths = {
            "pkexec": "/usr/bin/pkexec",
            "docking-camshield-helper": "/usr/bin/docking-camshield-helper",
        }
        monkeypatch.setattr(camshield_applet_mod.shutil, "which", paths.get)

        assert camshield_applet_mod._helper_command(action="lock") == [
            "/usr/bin/pkexec",
            "/usr/bin/docking-camshield-helper",
            "lock",
        ]

    def test_helper_command_falls_back_to_source_helper(self, monkeypatch, tmp_path):
        helper_path = tmp_path / "helper.py"
        helper_path.touch()
        monkeypatch.setattr(
            camshield_applet_mod.shutil,
            "which",
            lambda command: "/usr/bin/pkexec" if command == "pkexec" else None,
        )
        monkeypatch.setattr(camshield_applet_mod, "_SOURCE_HELPER", helper_path)

        assert camshield_applet_mod._helper_command(action="unlock") == [
            "/usr/bin/pkexec",
            camshield_applet_mod.sys.executable,
            str(helper_path),
            "unlock",
        ]

    def test_helper_command_returns_none_without_pkexec_or_helper(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(camshield_applet_mod.shutil, "which", lambda _command: None)
        monkeypatch.setattr(
            camshield_applet_mod, "_SOURCE_HELPER", tmp_path / "missing"
        )

        assert camshield_applet_mod._helper_command(action="lock") is None
