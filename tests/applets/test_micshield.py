"""Tests for Mic Shield."""

from __future__ import annotations

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

    def test_probe_result_updates_state(self):
        applet = _make_applet()

        assert applet._on_probe_result(_active_state()) is False

        assert applet._state == _active_state()
        assert applet.item.icon is not None

    def test_pulse_tick_advances_phase_and_notifies(self):
        applet = _make_applet(_active_state())
        applet._notify = MagicMock()

        assert applet._pulse_tick() is True

        assert applet._pulse_phase > 0.0
        applet._notify.assert_called_once()
