"""Native audio command parsing and writes; no hardware is changed by tests."""

import pytest

from docking.applets.volume.implementations.bsd import backend as bsd
from docking.applets.volume.implementations.bsd.backend import create_backend
from docking.applets.volume.state import VolumeState
from docking.platform import environment


@pytest.mark.parametrize(
    "system,volume,mute",
    [("openbsd7", "0.65", "1"), ("freebsd15", "vol.volume=0.65:0.65", "vol.mute=on")],
)
def test_native_audio_reads_volume_and_mute(monkeypatch, system, volume, mute):
    monkeypatch.setattr(bsd.sys, "platform", system)
    monkeypatch.setattr(
        bsd.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"sndioctl", "mixer"} else None,
    )
    monkeypatch.setattr(
        bsd,
        "run_command",
        lambda command, action: mute if "mute" in command[-1] else volume,
    )
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    backend = create_backend()
    assert backend is not None
    assert backend.get_state() == VolumeState(65, True)


@pytest.mark.parametrize(
    "system,expected",
    [
        ("openbsd7", ["sndioctl", "output.level=0.50"]),
        ("freebsd15", ["mixer", "vol.volume=0.50"]),
    ],
)
def test_native_audio_writes_fractional_volume(monkeypatch, system, expected):
    calls = []
    monkeypatch.setattr(bsd.sys, "platform", system)
    monkeypatch.setattr(
        bsd.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"sndioctl", "mixer"} else None,
    )
    monkeypatch.setattr(
        bsd, "run_command", lambda command, action: calls.append(command)
    )
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    backend = create_backend()
    assert backend is not None
    backend.set_volume(50)
    assert calls == [expected]


def test_missing_audio_tool_is_unavailable(monkeypatch):
    monkeypatch.setattr(bsd.shutil, "which", lambda name: None)
    assert bsd.create_backend() is None


def test_bsd_pulseaudio_preference_is_preserved(monkeypatch):
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    monkeypatch.setattr(
        bsd.shutil, "which", lambda name: "/bin/pactl" if name == "pactl" else None
    )
    backend = create_backend()
    assert backend is not None
    assert backend.command == "pactl"
