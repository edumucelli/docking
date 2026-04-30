"""Microphone mute and privacy-state helpers.

Mic Shield mirrors Cam Shield's user-facing idea, but the implementation has
to live at the audio-server layer.  Camera apps usually hold ``/dev/video*``
directly, so Cam Shield can inspect process file descriptors.  Microphone apps
usually capture through PulseAudio or PipeWire, so the process holding
``/dev/snd/*`` is often only the audio server.  ``pactl`` exposes the useful
view: real input-source mute state and current source-output capture streams.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

from docking.i18n import _
from docking.log import get_logger, with_context

log = with_context(get_logger(name="micshield.state"))

DEFAULT_POLL_INTERVAL_S = 2
MAX_TOOLTIP_STREAMS = 6
PACTL_BIN = "pactl"

_SOURCE_OUTPUT_RE = re.compile(r"^Source Output #(?P<id>\d+)\s*$")
_PROPERTY_RE = re.compile(r"^\s*(?P<key>[-.\w]+)\s*=\s*(?P<value>.*)\s*$")


@dataclass(frozen=True, slots=True)
class MicStream:
    """One active microphone capture stream reported by the audio server."""

    stream_id: int
    command: str
    pid: int | None = None
    name: str = ""


@dataclass(frozen=True, slots=True)
class MicShieldState:
    """Current microphone privacy state."""

    available: bool
    muted: bool
    active: bool
    streams: tuple[MicStream, ...] = ()


def probe_mic_state() -> MicShieldState:
    """Read microphone mute state and active capture streams."""
    if shutil.which(PACTL_BIN) is None:
        return MicShieldState(available=False, muted=False, active=False)

    source_names = input_source_names()
    mute_states = _source_mute_states(source_names=source_names)
    if not mute_states:
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
    """Set microphone sources and active capture streams to one mute state."""
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
    for stream in active_source_outputs():
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
    out = _run(
        cmd=[PACTL_BIN, "list", "source-outputs"],
        action="list_source_outputs",
    )
    return parse_source_outputs(output=out or "")


def parse_source_mute(*, output: str) -> bool | None:
    """Parse ``pactl get-source-mute`` output."""
    text = output.lower()
    if "yes" in text:
        return True
    if "no" in text:
        return False
    return None


def parse_input_sources(*, output: str) -> tuple[str, ...]:
    """Parse ``pactl list sources short`` and drop output monitor sources."""
    names: list[str] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[1]
        if name.endswith(".monitor") or ".monitor." in name:
            continue
        names.append(name)
    return tuple(names)


def parse_source_outputs(*, output: str) -> tuple[MicStream, ...]:
    """Parse active capture streams from ``pactl list source-outputs``.

    Corked streams are ignored because they are connected but not actively
    capturing.  Missing ``Corked`` is treated as active for older pactl output.
    """
    streams: list[MicStream] = []
    for stream_id, lines in _source_output_blocks(output=output):
        props = _parse_properties(lines=lines)
        corked = _parse_corked(lines=lines)
        if corked is True:
            continue
        command = (
            props.get("application.name")
            or props.get("application.process.binary")
            or props.get("media.name")
            or _("Unknown")
        )
        streams.append(
            MicStream(
                stream_id=stream_id,
                command=command,
                pid=_parse_pid(props.get("application.process.id")),
                name=props.get("media.name", ""),
            )
        )
    return tuple(streams)


def build_tooltip(state: MicShieldState) -> str:
    """Build compact tooltip text."""
    lines = [_("Mic Shield")]
    if not state.available:
        lines.append(_("No microphone source found"))
        return "\n".join(lines)

    lines.append(_("Microphone muted") if state.muted else _("Microphone unmuted"))
    if not state.active:
        lines.append(_("Microphone idle"))
        return "\n".join(lines)

    lines.append(_("Microphone active"))
    for stream in state.streams[:MAX_TOOLTIP_STREAMS]:
        lines.append(stream_label(stream))
    remaining = len(state.streams) - MAX_TOOLTIP_STREAMS
    if remaining > 0:
        lines.append(_("{count} more").format(count=remaining))
    return "\n".join(lines)


def stream_label(stream: MicStream) -> str:
    """Menu label for one active capture stream."""
    if stream.pid is None:
        return stream.command
    return _("{command} (PID {pid})").format(
        command=stream.command,
        pid=stream.pid,
    )


def _source_mute_states(*, source_names: tuple[str, ...]) -> tuple[bool, ...]:
    names = source_names or ("@DEFAULT_SOURCE@",)
    states: list[bool] = []
    for source_name in names:
        mute_out = _run(
            cmd=[PACTL_BIN, "get-source-mute", source_name],
            action="get_source_mute",
        )
        muted = parse_source_mute(output=mute_out or "")
        if muted is not None:
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


def _source_output_blocks(*, output: str) -> tuple[tuple[int, tuple[str, ...]], ...]:
    blocks: list[tuple[int, tuple[str, ...]]] = []
    current_id: int | None = None
    current_lines: list[str] = []
    for line in output.splitlines():
        match = _SOURCE_OUTPUT_RE.match(line.strip())
        if match:
            if current_id is not None:
                blocks.append((current_id, tuple(current_lines)))
            current_id = int(match.group("id"))
            current_lines = []
            continue
        if current_id is not None:
            current_lines.append(line)
    if current_id is not None:
        blocks.append((current_id, tuple(current_lines)))
    return tuple(blocks)


def _parse_properties(*, lines: tuple[str, ...]) -> dict[str, str]:
    properties: dict[str, str] = {}
    in_properties = False
    for line in lines:
        if line.strip() == "Properties:":
            in_properties = True
            continue
        if not in_properties:
            continue
        match = _PROPERTY_RE.match(line)
        if not match:
            continue
        properties[match.group("key")] = _unquote_property(match.group("value"))
    return properties


def _parse_corked(*, lines: tuple[str, ...]) -> bool | None:
    for line in lines:
        stripped = line.strip().lower()
        if not stripped.startswith("corked:"):
            continue
        if "yes" in stripped:
            return True
        if "no" in stripped:
            return False
    return None


def _parse_pid(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _unquote_property(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return stripped[1:-1]
    return stripped
