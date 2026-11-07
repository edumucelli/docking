"""NetBSD mixer controls, including mixers without a dedicated mute switch."""

from __future__ import annotations

import shutil

from docking.applets.volume.backend import VolumeControls
from docking.applets.volume.implementations.unix.command import run_command
from docking.applets.volume.state import VolumeState


def controls() -> VolumeControls | None:
    if shutil.which("mixerctl") is None:
        return None
    saved_levels: tuple[int, ...] | None = None

    def read() -> tuple[str, tuple[int, ...], str | None, bool] | None:
        nonlocal saved_levels
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
        if any(levels):
            saved_levels = None
        muted = values[mute] == "on" if mute else saved_levels is not None
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
        nonlocal saved_levels
        reading = read()
        if reading is None:
            return
        key, levels, _mute, _muted = reading
        level = round(max(0, min(100, value)) * 255 / 100)
        if write(key, (level,) * len(levels)):
            saved_levels = None

    def toggle_mute() -> None:
        nonlocal saved_levels
        reading = read()
        if reading is None:
            return
        key, levels, mute, muted = reading
        if mute is not None:
            run_command(
                ["mixerctl", "-w", f"{mute}={'off' if muted else 'on'}"], "toggle_mute"
            )
        elif saved_levels is not None:
            if write(key, saved_levels):
                saved_levels = None
        elif any(levels) and write(key, (0,) * len(levels)):
            saved_levels = levels

    return VolumeControls("mixerctl", get_state, set_volume, toggle_mute)
