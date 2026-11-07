"""NetBSD mixer channel scaling, native mute, and reversible software mute."""

import pytest

from docking.applets.volume.implementations.bsd import backend, netbsd
from docking.applets.volume.state import VolumeState


@pytest.fixture
def mixer(monkeypatch):
    values = {"outputs.master": "128,204", "outputs.master.mute": "off"}
    calls = []

    def command(argv, _action):
        calls.append(argv)
        assert argv[0] == "mixerctl"
        if argv[1] == "-w":
            key, value = argv[2].split("=", 1)
            values[key] = value
        return "\n".join(f"{key}={value}" for key, value in values.items())

    monkeypatch.setattr(netbsd, "run_command", command)
    monkeypatch.setattr(backend.sys, "platform", "netbsd11")
    monkeypatch.setattr(
        backend.shutil,
        "which",
        lambda name: "/usr/bin/mixerctl" if name == "mixerctl" else None,
    )
    return values, calls, backend.create_backend().controls


def test_native_mixer_scales_channels_and_toggles_hardware_mute(mixer):
    values, calls, controls = mixer
    assert controls.get_state() == VolumeState(65, False)
    controls.set_volume(50)
    assert values["outputs.master"] == "128,128"
    controls.toggle_mute()
    assert controls.get_state() == VolumeState(50, True)
    controls.toggle_mute()
    assert controls.get_state() == VolumeState(50, False)
    assert ["mixerctl", "-w", "outputs.master.mute=on"] in calls


def test_missing_mute_control_restores_original_channel_balance(mixer):
    values, _calls, controls = mixer
    del values["outputs.master.mute"]
    controls.toggle_mute()
    assert controls.get_state() == VolumeState(0, True)
    controls.toggle_mute()
    assert values["outputs.master"] == "128,204"
    assert controls.get_state() == VolumeState(65, False)


def test_external_volume_change_cancels_software_mute(mixer):
    values, _calls, controls = mixer
    del values["outputs.master.mute"]
    controls.toggle_mute()
    values["outputs.master"] = "51,51"
    assert controls.get_state() == VolumeState(20, False)
    controls.toggle_mute()
    controls.toggle_mute()
    assert values["outputs.master"] == "51,51"


@pytest.mark.parametrize("volume", ["bad", "-1,255", "256", ""])
def test_invalid_levels_are_unavailable_and_never_written(mixer, volume):
    values, calls, controls = mixer
    values["outputs.master"] = volume
    assert controls.get_state() is None
    controls.set_volume(50)
    controls.toggle_mute()
    assert all(argv[1] != "-w" for argv in calls)


def test_failed_write_does_not_claim_software_mute(mixer, monkeypatch):
    values, _calls, controls = mixer
    del values["outputs.master.mute"]
    read = netbsd.run_command
    monkeypatch.setattr(
        netbsd,
        "run_command",
        lambda argv, action: None if argv[1] == "-w" else read(argv, action),
    )
    controls.toggle_mute()
    assert controls.get_state() == VolumeState(65, False)


def test_pulseaudio_keeps_precedence_on_netbsd(monkeypatch):
    monkeypatch.setattr(backend.sys, "platform", "netbsd11")
    monkeypatch.setattr(backend.shutil, "which", lambda _name: "/usr/pkg/bin/pactl")
    assert backend.create_backend().controls.command == "pactl"
