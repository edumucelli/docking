"""Unix microphone capture and mute operations."""

from __future__ import annotations

import os
import shutil
import subprocess

from docking.applets.micshield.backend import (
    MicshieldOperations as MicshieldOperationsBase,
)

from ...state import (
    PACTL_BIN,
    MicShieldState,
    MicStream,
    log,
    parse_input_sources,
    parse_source_mute,
    parse_source_outputs,
)


def probe_mic_state() -> MicShieldState:
    """Read microphone mute state and active capture streams."""
    if shutil.which(PACTL_BIN) is None:
        return MicShieldState(available=False, muted=False, active=False)

    source_names = input_source_names()
    mute_states = _source_mute_states(source_names=source_names)
    if not mute_states or any(muted is None for muted in mute_states):
        return MicShieldState(available=False, muted=False, active=False)

    streams = active_source_outputs()
    return MicShieldState(
        available=True,
        muted=all(mute_states),
        active=bool(streams),
        streams=streams,
    )


def toggle_mic_mute() -> bool:
    """Toggle all microphone inputs between muted and unmuted.

    Toggling only ``@DEFAULT_SOURCE@`` misses apps that opened a specific input
    source before the default changed.  Mic Shield treats the control as a
    privacy mute, so it applies the target state to every real input source.
    """
    state = probe_mic_state()
    if not state.available:
        return False
    return set_mic_muted(muted=not state.muted)


def set_mic_muted(*, muted: bool) -> bool:
    """Set microphone sources and connected capture streams to one mute state."""
    if shutil.which(PACTL_BIN) is None:
        return False
    source_names = input_source_names()
    if not source_names:
        source_names = ("@DEFAULT_SOURCE@",)
    changed = False
    for source_name in source_names:
        changed = (
            _run(
                cmd=[
                    PACTL_BIN,
                    "set-source-mute",
                    source_name,
                    "1" if muted else "0",
                ],
                action="set_source_mute",
            )
            is not None
            or changed
        )
    for stream in _source_outputs(include_corked=True):
        changed = (
            _run(
                cmd=[
                    PACTL_BIN,
                    "set-source-output-mute",
                    str(stream.stream_id),
                    "1" if muted else "0",
                ],
                action="set_source_output_mute",
            )
            is not None
            or changed
        )
    return changed


def input_source_names() -> tuple[str, ...]:
    """Return real microphone input source names, excluding monitor sources."""
    out = _run(
        cmd=[PACTL_BIN, "list", "sources", "short"],
        action="list_sources",
    )
    return parse_input_sources(output=out or "")


def active_source_outputs() -> tuple[MicStream, ...]:
    """Return active source-output streams."""
    return _source_outputs(include_corked=False)


def _source_outputs(*, include_corked: bool) -> tuple[MicStream, ...]:
    out = _run(
        cmd=[PACTL_BIN, "list", "source-outputs"],
        action="list_source_outputs",
    )
    return parse_source_outputs(output=out or "", include_corked=include_corked)


def _source_mute_states(*, source_names: tuple[str, ...]) -> tuple[bool | None, ...]:
    names = source_names or ("@DEFAULT_SOURCE@",)
    states: list[bool | None] = []
    for source_name in names:
        mute_out = _run(
            cmd=[PACTL_BIN, "get-source-mute", source_name],
            action="get_source_mute",
        )
        muted = parse_source_mute(output=mute_out or "")
        states.append(muted)
    return tuple(states)


def _run(*, cmd: list[str], action: str) -> str | None:
    """Run one pactl command and return stdout on success."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.bind(action=action).warning("Failed to run %s: %s", cmd, exc)
        return None
    if result.returncode != 0:
        log.bind(action=action).debug(
            "Command %s exited %s: %s",
            cmd,
            result.returncode,
            result.stderr.strip(),
        )
        return None
    return result.stdout


class MicshieldOperations(MicshieldOperationsBase):
    def probe_mic_state(self) -> MicShieldState:
        return probe_mic_state()

    def toggle_mic_mute(self) -> bool:
        return toggle_mic_mute()

    def set_mic_muted(self, *, muted: bool) -> bool:
        return set_mic_muted(muted=muted)

    def input_source_names(self) -> tuple[str, ...]:
        return input_source_names()

    def active_source_outputs(self) -> tuple[MicStream, ...]:
        return active_source_outputs()
