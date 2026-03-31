"""CLI entry point for AI usage hooks.

Claude Code invocation:
    python3 -m docking.applets.aiusage.hook claude Stop  (JSON via stdin)
    python3 -m docking.applets.aiusage.hook claude SessionStart

Codex CLI invocation (via notify):
    python3 -m docking.applets.aiusage.hook codex <json_arg>

No GTK imports -- this runs as a standalone subprocess.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

from docking.applets.aiusage import state as aiusage_state
from docking.log import get_logger

PREFS_KEY = "aiusage"
PREFS_KEY_LEGACY = "claude"
log = get_logger("aiusage.hook")


def _config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "docking" / "dock.json"


def _update_config(session_id: str, model_usage: dict) -> None:
    """Atomically read-modify-write aiusage prefs in dock.json."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "r") as f:
            try:
                config = json.load(f)
            except (json.JSONDecodeError, ValueError) as exc:
                log.debug(
                    "Failed to parse %s while updating AI usage config: %s",
                    path,
                    exc,
                )
                config = {}

        applet_prefs = config.setdefault("applet_prefs", {})
        prefs = applet_prefs.get(PREFS_KEY) or applet_prefs.get(PREFS_KEY_LEGACY, {})
        state = aiusage_state.state_from_prefs(prefs=prefs)
        state = aiusage_state.set_session(
            state=state,
            session_id=session_id,
            model_usage=model_usage,
        )
        applet_prefs[PREFS_KEY] = aiusage_state.prefs_from_state(state=state)

        data = json.dumps(config, indent=2) + "\n"
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, data.encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ------------------------------------------------------------------
# Claude handlers
# ------------------------------------------------------------------


def _handle_claude_stop(data: dict) -> None:
    transcript_path = data.get("transcript_path")
    if not transcript_path:
        return
    session_id = data.get("session_id") or Path(transcript_path).stem
    model_usage = aiusage_state.parse_claude_transcript(path=Path(transcript_path))
    if not model_usage:
        return
    _update_config(session_id=session_id, model_usage=model_usage)


# ------------------------------------------------------------------
# Codex handlers
# ------------------------------------------------------------------


def _handle_codex_turn(json_arg: str) -> None:
    try:
        data = json.loads(json_arg)
    except (json.JSONDecodeError, ValueError) as exc:
        log.debug("Failed to parse Codex hook payload %r: %s", json_arg, exc)
        data = {}

    thread_id = data.get("thread-id")
    session_path = aiusage_state.find_codex_session(thread_id=thread_id)
    if not session_path:
        return
    session_id = thread_id or session_path.stem
    model_usage = aiusage_state.parse_codex_transcript(path=session_path)
    if not model_usage:
        return
    _update_config(session_id=session_id, model_usage=model_usage)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main() -> None:
    """Dispatch based on provider (argv[1]) and event."""
    if len(sys.argv) < 2:
        return

    provider = sys.argv[1]

    if provider == "claude":
        event = sys.argv[2] if len(sys.argv) > 2 else ""
        try:
            data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError) as exc:
            log.debug("Failed to parse Claude hook stdin payload: %s", exc)
            return
        if event == "Stop":
            _handle_claude_stop(data=data)

    elif provider == "codex":
        # Codex appends the JSON payload as the last CLI arg.
        json_arg = sys.argv[-1] if len(sys.argv) > 2 else "{}"
        _handle_codex_turn(json_arg=json_arg)


if __name__ == "__main__":
    main()
