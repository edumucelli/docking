"""Audio command output and mute state must survive locale and paused streams."""

import json
import os

import pytest

import docking.applets.micshield.implementations.unix.backend as native_micshield
from docking.applets.volume.implementations.unix import pulseaudio as volume
from docking.applets.volume.state import VolumeState


@pytest.fixture
def pactl(tmp_path, monkeypatch):
    """Model the CLI output captured from the real isolated PulseAudio probe."""
    state_file = tmp_path / "state.json"
    state = {"sources": {"microphone": True}, "corked": False, "stream_muted": False}
    state_file.write_text(json.dumps(state))
    command = tmp_path / "pactl"
    command.write_text(
        """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['AUDIT_PULSE_STATE'])
state = json.loads(p.read_text())
args = sys.argv[1:]
fr = os.environ.get('LC_ALL') != 'C'
if args == ['list', 'sources', 'short']:
    for i, name in enumerate(state['sources']):
        print(f'{i}\\t{name}\\tPipeWire')
elif args[:1] == ['get-source-mute']:
    muted = state['sources'][args[1]]
    if muted is None:
        raise SystemExit(1)
    print('Mute: ' + (('oui' if muted else 'non') if fr else ('yes' if muted else 'no')))
elif args[:1] == ['get-sink-mute']:
    print('Mute: ' + ('oui' if fr else 'yes'))
elif args[:1] == ['get-sink-volume']:
    print('Volume: mono: 32768 / 50% / -18.06 dB')
elif args == ['list', 'source-outputs']:
    print(('Sortie de la source' if fr else 'Source Output') + ' #42')
    print(('    Suspendu : ' if fr else '    Corked: ') + ('yes' if state['corked'] else 'no'))
    print('    Properties:\\n        application.name = "Recorder"')
elif args[:1] == ['set-source-mute']:
    state['sources'][args[1]] = args[2] == '1'
elif args[:1] == ['set-source-output-mute']:
    state['stream_muted'] = args[2] == '1'
else:
    raise SystemExit('Unexpected pactl command: ' + repr(args))
p.write_text(json.dumps(state))
"""
    )
    command.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ["PATH"])
    monkeypatch.setenv("AUDIT_PULSE_STATE", str(state_file))
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("LANGUAGE", "fr")
    return state_file


@pytest.mark.parametrize("kind", ["volume", "mic_mute", "capture"])
def test_localized_pactl_output_is_parsed(pactl, kind):
    if kind == "volume":
        assert volume._pactl_get_state() == VolumeState(volume=50, muted=True)
    elif kind == "mic_mute":
        state = native_micshield.probe_mic_state()
        assert state.available
        assert state.muted
    else:
        assert [
            stream.stream_id for stream in native_micshield.active_source_outputs()
        ] == [42]


def test_unmute_also_restores_a_paused_recording(pactl, monkeypatch):
    monkeypatch.setenv("LC_ALL", "C")
    state = json.loads(pactl.read_text())
    state.update(corked=True, stream_muted=True)
    pactl.write_text(json.dumps(state))

    assert native_micshield.set_mic_muted(muted=False)

    assert not json.loads(pactl.read_text())["stream_muted"]
    assert native_micshield.active_source_outputs() == ()


@pytest.mark.parametrize("known_mute", [True, False])
def test_partial_mute_query_disables_toggle(pactl, monkeypatch, known_mute):
    monkeypatch.setenv("LC_ALL", "C")
    state = json.loads(pactl.read_text())
    state["sources"]["microphone"] = known_mute
    state["sources"]["unread_microphone"] = None
    pactl.write_text(json.dumps(state))

    actual = native_micshield.probe_mic_state()

    assert not actual.available
    assert not actual.muted
    assert not native_micshield.toggle_mic_mute()
    assert json.loads(pactl.read_text())["sources"] == state["sources"]
