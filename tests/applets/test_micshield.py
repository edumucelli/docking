"""Tests for Mic Shield."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import docking.applets.micshield.applet as micshield_applet_mod
import docking.applets.micshield.state as micshield_state_mod
from docking.applets.micshield.applet import MicShieldApplet
from docking.applets.micshield.render import render_icon
from docking.applets.micshield.state import (
    MicShieldState,
    MicStream,
    build_tooltip,
    parse_input_sources,
    parse_source_mute,
    parse_source_outputs,
    probe_mic_state,
    set_mic_muted,
    stream_label,
    toggle_mic_mute,
)


class _ImmediateWorker:
    def __init__(self, **_kwargs) -> None:
        pass

    def run(self, *, fn, on_result=None, on_error=None, **_kwargs) -> None:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)


def _active_state() -> MicShieldState:
    return MicShieldState(
        available=True,
        muted=False,
        active=True,
        streams=(
            MicStream(
                stream_id=42,
                command="Firefox",
                pid=1234,
                name="AudioStream",
            ),
        ),
    )


def _make_applet(state: MicShieldState | None = None) -> MicShieldApplet:
    with (
        patch("docking.applets.micshield.applet.BackgroundWorker", _ImmediateWorker),
        patch(
            "docking.applets.micshield.applet.probe_mic_state",
            lambda: state or MicShieldState(False, False, False),
        ),
    ):
        return MicShieldApplet(48)


SOURCE_OUTPUTS = """
Source Output #42
    Corked: no
    Properties:
        application.name = "Firefox"
        application.process.id = "1234"
        media.name = "AudioStream"
Source Output #43
    Corked: yes
    Properties:
        application.name = "Idle App"
        application.process.id = "5678"
""".strip()


class TestMicShieldState:
    def test_parse_source_mute(self):
        assert parse_source_mute(output="Mute: yes") is True
        assert parse_source_mute(output="Mute: no") is False
        assert parse_source_mute(output="bad") is None

    def test_parse_input_sources_filters_monitors(self):
        output = """
0\talsa_output.pci.stereo.monitor\tPipeWire\tfloat32le 2ch 48000Hz\tIDLE
1\talsa_input.pci.mic\tPipeWire\tfloat32le 2ch 48000Hz\tRUNNING
2\tbluez_input.headset\tPipeWire\tfloat32le 1ch 48000Hz\tIDLE
""".strip()
        assert parse_input_sources(output=output) == (
            "alsa_input.pci.mic",
            "bluez_input.headset",
        )

    def test_parse_source_outputs_active_only(self):
        assert parse_source_outputs(output=SOURCE_OUTPUTS) == (
            MicStream(
                stream_id=42,
                command="Firefox",
                pid=1234,
                name="AudioStream",
            ),
        )

    def test_parse_source_outputs_missing_corked_is_active(self):
        output = """
Source Output #7
    Properties:
        application.process.binary = "arecord"
""".strip()
        assert parse_source_outputs(output=output) == (
            MicStream(stream_id=7, command="arecord"),
        )

    def test_parse_source_outputs_fallbacks_and_invalid_pid(self):
        output = """
Source Output #8
    Corked: no
    Properties:
        media.name = "Raw capture"
        application.process.id = "bad"
Source Output #9
    Corked: maybe
    Properties:
        application.process.id = "9"
""".strip()

        assert parse_source_outputs(output=output) == (
            MicStream(stream_id=8, command="Raw capture", name="Raw capture"),
            MicStream(stream_id=9, command="Unknown", pid=9),
        )

    def test_probe_unavailable_without_pactl(self, monkeypatch):
        monkeypatch.setattr(micshield_state_mod.shutil, "which", lambda _cmd: None)

        assert probe_mic_state() == MicShieldState(
            available=False,
            muted=False,
            active=False,
        )

    def test_probe_reads_mute_and_streams(self, monkeypatch):
        monkeypatch.setattr(
            micshield_state_mod.shutil,
            "which",
            lambda _cmd: "/usr/bin/pactl",
        )

        def run(*, cmd, action):
            assert cmd[0] == "pactl"
            if action == "list_sources":
                return "1\talsa_input.pci.mic\tPipeWire"
            if action == "get_source_mute":
                return "Mute: no"
            return SOURCE_OUTPUTS

        monkeypatch.setattr(micshield_state_mod, "_run", run)

        assert probe_mic_state() == _active_state()

    def test_probe_without_mute_states_is_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            micshield_state_mod.shutil,
            "which",
            lambda _cmd: "/usr/bin/pactl",
        )
        monkeypatch.setattr(micshield_state_mod, "input_source_names", lambda: ("mic",))
        monkeypatch.setattr(
            micshield_state_mod,
            "_source_mute_states",
            lambda **_kwargs: (),
        )

        assert probe_mic_state() == MicShieldState(False, False, False)

    def test_toggle_returns_false_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            micshield_state_mod,
            "probe_mic_state",
            lambda: MicShieldState(False, False, False),
        )

        assert toggle_mic_mute() is False

    def test_set_mic_muted_returns_false_without_pactl(self, monkeypatch):
        monkeypatch.setattr(micshield_state_mod.shutil, "which", lambda _cmd: None)

        assert set_mic_muted(muted=True) is False

    def test_set_mic_muted_uses_default_source_when_sources_missing(self, monkeypatch):
        seen: list[list[str]] = []
        monkeypatch.setattr(
            micshield_state_mod.shutil,
            "which",
            lambda _cmd: "/usr/bin/pactl",
        )
        monkeypatch.setattr(micshield_state_mod, "input_source_names", lambda: ())
        monkeypatch.setattr(micshield_state_mod, "active_source_outputs", lambda: ())
        monkeypatch.setattr(
            micshield_state_mod,
            "_run",
            lambda *, cmd, action: seen.append(cmd) or None,
        )

        assert set_mic_muted(muted=False) is False
        assert seen == [["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "0"]]

    def test_toggle_mic_mute_calls_pactl(self, monkeypatch):
        seen: list[list[str]] = []
        monkeypatch.setattr(
            micshield_state_mod.shutil,
            "which",
            lambda _cmd: "/usr/bin/pactl",
        )

        def run(*, cmd, action):
            if action == "list_sources":
                return (
                    "0\talsa_output.pci.stereo.monitor\tPipeWire\n"
                    "1\talsa_input.pci.mic\tPipeWire\n"
                    "2\tbluez_input.headset\tPipeWire"
                )
            if action == "get_source_mute":
                return "Mute: no"
            if action == "list_source_outputs":
                return ""
            seen.append(cmd)
            return ""

        monkeypatch.setattr(micshield_state_mod, "_run", run)

        assert toggle_mic_mute() is True
        assert seen == [
            ["pactl", "set-source-mute", "alsa_input.pci.mic", "1"],
            ["pactl", "set-source-mute", "bluez_input.headset", "1"],
        ]

    def test_set_mic_muted_mutes_sources_and_active_streams(self, monkeypatch):
        seen: list[list[str]] = []
        monkeypatch.setattr(
            micshield_state_mod.shutil,
            "which",
            lambda _cmd: "/usr/bin/pactl",
        )

        def run(*, cmd, action):
            if action == "list_sources":
                return "1\talsa_input.pci.mic\tPipeWire"
            if action == "list_source_outputs":
                return SOURCE_OUTPUTS
            seen.append(cmd)
            return ""

        monkeypatch.setattr(micshield_state_mod, "_run", run)

        assert set_mic_muted(muted=True) is True
        assert seen == [
            ["pactl", "set-source-mute", "alsa_input.pci.mic", "1"],
            ["pactl", "set-source-output-mute", "42", "1"],
        ]

    def test_tooltip_and_stream_label(self):
        tooltip = build_tooltip(_active_state())
        assert "Mic Shield" in tooltip
        assert "Microphone active" in tooltip
        assert stream_label(_active_state().streams[0]) == "Firefox (PID 1234)"

    def test_tooltip_unavailable_idle_and_more_streams(self):
        assert "No microphone source found" in build_tooltip(
            MicShieldState(False, False, False)
        )
        assert "Microphone idle" in build_tooltip(MicShieldState(True, True, False))
        streams = tuple(MicStream(i, f"app{i}") for i in range(8))
        tooltip = build_tooltip(MicShieldState(True, False, True, streams))

        assert "2 more" in tooltip
        assert stream_label(MicStream(1, "arecord")) == "arecord"

    def test_source_mute_states_and_command_helpers(self, monkeypatch):
        outputs = iter(["Mute: yes", "bad", "Mute: no"])
        monkeypatch.setattr(
            micshield_state_mod,
            "_run",
            lambda **_kwargs: next(outputs),
        )

        assert micshield_state_mod._source_mute_states(
            source_names=("mic1", "mic2", "mic3")
        ) == (True, False)

    def test_run_handles_success_failure_and_exceptions(self, monkeypatch):
        monkeypatch.setattr(
            micshield_state_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout="ok",
                stderr="",
            ),
        )
        assert micshield_state_mod._run(cmd=["pactl"], action="test") == "ok"

        monkeypatch.setattr(
            micshield_state_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="bad",
            ),
        )
        assert micshield_state_mod._run(cmd=["pactl"], action="test") is None

        monkeypatch.setattr(
            micshield_state_mod.subprocess,
            "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(["pactl"], timeout=2)
            ),
        )
        assert micshield_state_mod._run(cmd=["pactl"], action="test") is None


class TestMicShieldRender:
    def test_renders_states(self):
        for muted, active in ((False, False), (True, False), (False, True)):
            pixbuf = render_icon(
                size=48,
                available=True,
                muted=muted,
                active=active,
                pulse_phase=0.5,
            )
            assert pixbuf is not None
            assert pixbuf.get_width() == 48
            assert pixbuf.get_height() == 48


class TestMicShieldApplet:
    def test_creates_with_icon_and_tooltip(self):
        applet = _make_applet(_active_state())

        assert applet.item.icon is not None
        assert "Mic Shield" in applet.item.name
        assert "Microphone active" in applet.item.name

    def test_start_schedules_poll_and_pulse(self, monkeypatch):
        idle = MagicMock()
        add_seconds = MagicMock(return_value=11)
        pulse_add = MagicMock(return_value=42)
        monkeypatch.setattr(micshield_applet_mod.GLib, "idle_add", idle)
        monkeypatch.setattr(
            micshield_applet_mod.GLib,
            "timeout_add_seconds",
            add_seconds,
        )
        monkeypatch.setattr(micshield_applet_mod.GLib, "timeout_add", pulse_add)
        applet = _make_applet(_active_state())

        applet.start(lambda: None)

        idle.assert_called_once()
        assert applet._timer_id == 11
        assert applet._pulse_timer_id == 42

    def test_stop_removes_poll_and_pulse_timers(self, monkeypatch):
        applet = _make_applet(_active_state())
        applet._timer_id = 11
        applet._pulse_timer_id = 42
        removed: list[int] = []
        monkeypatch.setattr(
            micshield_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.stop()

        assert removed == [11, 42]
        assert applet._timer_id == 0
        assert applet._pulse_timer_id == 0

    def test_refresh_once_tick_and_refresh_now_use_worker(self, monkeypatch):
        applet = _make_applet()
        applet._refresh_now = MagicMock()

        assert applet._refresh_once() is False
        assert applet._tick() is True
        assert applet._refresh_now.call_count == 2

        applet = _make_applet()
        applet._worker = MagicMock()
        applet._refresh_now()

        assert applet._worker.run.call_args.kwargs["name"] == "micshield-poll"

    def test_on_clicked_toggles_and_refreshes(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            micshield_applet_mod,
            "toggle_mic_mute",
            lambda: calls.append("toggle") or True,
        )
        applet = _make_applet(_active_state())
        applet._refresh_now = MagicMock()

        applet.on_clicked()

        assert calls == ["toggle"]
        applet._refresh_now.assert_called_once()

    def test_toggle_and_set_muted_ignore_unavailable_state(self):
        applet = _make_applet(MicShieldState(False, False, False))
        applet._worker = MagicMock()

        applet.on_clicked()
        applet._set_muted(True)

        applet._worker.run.assert_not_called()

    def test_set_muted_runs_worker_and_refreshes(self, monkeypatch):
        calls: list[bool] = []
        monkeypatch.setattr(
            micshield_applet_mod,
            "set_mic_muted",
            lambda *, muted: calls.append(muted) or True,
        )
        applet = _make_applet(_active_state())
        applet._refresh_now = MagicMock()

        applet._set_muted(True)

        assert calls == [True]
        applet._refresh_now.assert_called_once()

    def test_probe_result_updates_state(self):
        applet = _make_applet()

        assert applet._on_probe_result(_active_state()) is False

        assert applet._state == _active_state()
        assert applet.item.icon is not None

    def test_ensure_pulse_timer_stops_when_idle(self, monkeypatch):
        applet = _make_applet(MicShieldState(True, False, False))
        applet._notify = lambda: None
        applet._pulse_timer_id = 42
        applet._pulse_phase = 0.5
        removed: list[int] = []
        monkeypatch.setattr(
            micshield_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet._ensure_pulse_timer()

        assert removed == [42]
        assert applet._pulse_timer_id == 0
        assert applet._pulse_phase == 0.0

    def test_menu_labels_for_unavailable_idle_and_muted_states(self):
        unavailable = _make_applet(MicShieldState(False, False, False))
        assert "No microphone source found" in [
            item.get_label() for item in unavailable.get_menu_items()
        ]

        muted = _make_applet(MicShieldState(True, True, False))
        labels = [item.get_label() for item in muted.get_menu_items()]

        assert "Microphone muted" in labels
        assert "Microphone idle" in labels
        assert "Unmute Microphone" in labels

    def test_pulse_tick_advances_phase_and_notifies(self):
        applet = _make_applet(_active_state())
        applet._notify = MagicMock()

        assert applet._pulse_tick() is True

        assert applet._pulse_phase > 0.0
        applet._notify.assert_called_once()
