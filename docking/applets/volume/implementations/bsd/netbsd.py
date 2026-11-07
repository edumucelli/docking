"""NetBSD mixer controls, including mixers without a dedicated mute switch."""

from __future__ import annotations

import shutil

from docking.applets.volume.backend import VolumeControls
from docking.applets.volume.implementations.unix.command import run_command
from docking.applets.volume.state import VolumeState
from docking.platform.environment import docking_state_dir

from .software_mute import SavedMute, load_mute, save_mute


def controls() -> VolumeControls | None:
    """Create native controls; perform mixer and recovery I/O on their worker."""
    if shutil.which("mixerctl") is None:
        return None
    state_path = docking_state_dir() / "volume/netbsd-mute.json"
    saved: SavedMute | None = None
    loaded = False

    def read() -> tuple[str, tuple[int, ...], str | None, bool] | None:
        nonlocal saved, loaded
        output = run_command(["mixerctl", "-a"], "get_state")
        if output is None:
            return None
        values = {}
        for line in output.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip()
        key = next(
            (
                name
                for name in ("outputs.master", "outputs.volume", "inputs.dac")
                if name in values
            ),
            None,
        )
        if key is None:
            return None
        try:
            levels = tuple(int(value) for value in values[key].split(","))
        except ValueError:
            return None
        if not levels or any(value < 0 or value > 255 for value in levels):
            return None
        mute = next(
            (
                name
                for name in (f"{key}.mute", "outputs.mute")
                if values.get(name) in {"on", "off"}
            ),
            None,
        )
        if not loaded:
            saved = load_mute(state_path)
            loaded = True
        if saved is not None and (
            saved.control != key
            or len(saved.levels) != len(levels)
            or mute is not None
            or any(levels)
        ):
            saved = None
            save_mute(state_path, None)
        muted = values[mute] == "on" if mute else saved is not None
        return key, levels, mute, muted

    def write(key: str, levels: tuple[int, ...]) -> bool:
        return (
            run_command(
                ["mixerctl", "-w", f"{key}=" + ",".join(map(str, levels))], "set_volume"
            )
            is not None
        )

    def get_state() -> VolumeState | None:
        reading = read()
        if reading is None:
            return None
        _key, levels, _mute, muted = reading
        return VolumeState(round(sum(levels) * 100 / (len(levels) * 255)), muted)

    def set_volume(value: int) -> None:
        nonlocal saved
        reading = read()
        if reading is None:
            return
        key, levels, _mute, _muted = reading
        level = round(max(0, min(100, value)) * 255 / 100)
        if write(key, (level,) * len(levels)):
            saved = None
            save_mute(state_path, None)

    def toggle_mute() -> None:
        nonlocal saved
        reading = read()
        if reading is None:
            return
        key, levels, mute, muted = reading
        if mute is not None:
            run_command(
                ["mixerctl", "-w", f"{mute}={'off' if muted else 'on'}"], "toggle_mute"
            )
        elif saved is not None:
            if write(key, saved.levels):
                saved = None
                save_mute(state_path, None)
        else:
            recovery = SavedMute(key, levels)
            if save_mute(state_path, recovery):
                if write(key, (0,) * len(levels)):
                    saved = recovery
                else:
                    save_mute(state_path, None)

    return VolumeControls("mixerctl", get_state, set_volume, toggle_mute)
