"""NetBSD mixer channel scaling, native mute, and reversible software mute."""

import sys

import pytest

from docking.applets.volume.implementations.bsd import backend, netbsd
from docking.applets.volume.state import VolumeState


@pytest.fixture
def mixer(monkeypatch, tmp_path):
    monkeypatch.setattr(netbsd, "docking_state_dir", lambda: tmp_path)
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
    monkeypatch.setattr(sys, "platform", "netbsd11")
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
    monkeypatch.setattr(sys, "platform", "netbsd11")
    monkeypatch.setattr(backend.shutil, "which", lambda _name: "/usr/pkg/bin/pactl")
    assert backend.create_backend().controls.command == "pactl"


def test_software_mute_restores_channels_after_backend_recreation(mixer):
    values, _calls, controls = mixer
    del values["outputs.master.mute"]
    controls.toggle_mute()
    restarted = backend.create_backend().controls
    assert restarted.get_state() == VolumeState(0, True)
    restarted.toggle_mute()
    assert values["outputs.master"] == "128,204"
    assert restarted.get_state() == VolumeState(65, False)
    assert backend.create_backend().controls.get_state() == VolumeState(65, False)


def test_software_mute_at_zero_is_remembered_and_can_be_unmuted(mixer):
    values, calls, controls = mixer
    del values["outputs.master.mute"]
    values["outputs.master"] = "0,0"
    controls.toggle_mute()
    assert ["mixerctl", "-w", "outputs.master=0,0"] in calls
    restarted = backend.create_backend().controls
    assert restarted.get_state() == VolumeState(0, True)
    restarted.toggle_mute()
    assert restarted.get_state() == VolumeState(0, False)


@pytest.mark.parametrize("change", ["volume", "control", "channels", "hardware-mute"])
def test_recreated_backend_discards_incompatible_or_external_mute_state(mixer, change):
    values, _calls, controls = mixer
    del values["outputs.master.mute"]
    controls.toggle_mute()
    if change == "volume":
        values["outputs.master"] = "51,51"
    elif change == "control":
        del values["outputs.master"]
        values["outputs.volume"] = "0,0"
    elif change == "channels":
        values["outputs.master"] = "0"
    else:
        values["outputs.master.mute"] = "off"
    restarted = backend.create_backend().controls
    assert not restarted.get_state().muted
    assert not (netbsd.docking_state_dir() / "volume/netbsd-mute.json").exists()


def test_mute_does_not_zero_channels_when_recovery_cannot_be_saved(mixer, tmp_path):
    values, calls, controls = mixer
    del values["outputs.master.mute"]
    (tmp_path / "volume").write_text("not a directory")
    controls.toggle_mute()
    assert controls.get_state() == VolumeState(65, False)
    assert not any(call[1] == "-w" for call in calls)


@pytest.mark.parametrize(
    "record",
    [
        "not JSON",
        '{"version":true,"control":"outputs.master","levels":[128,204]}',
        '{"version":1,"control":"outputs.master","levels":[true]}',
        '{"version":1,"control":"outputs.master","levels":[256]}',
        '{"version":1,"control":"outputs.master","levels":[]}',
    ],
)
def test_corrupt_mute_recovery_is_not_restored(mixer, tmp_path, record):
    values, _calls, _controls = mixer
    del values["outputs.master.mute"]
    values["outputs.master"] = "0,0"
    directory = tmp_path / "volume"
    directory.mkdir()
    (directory / "netbsd-mute.json").write_text(record)
    assert backend.create_backend().controls.get_state() == VolumeState(0, False)
