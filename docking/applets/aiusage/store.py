"""Persistent storage helpers for AI usage hooks."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

from docking.applets.aiusage import state as aiusage_state
from docking.log import get_logger

PREFS_KEY = "aiusage"
PREFS_KEY_LEGACY = "claude"

log = get_logger("aiusage.store")


def config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "docking" / "dock.json"


def read_prefs_from_disk() -> dict | None:
    """Read aiusage prefs directly from dock.json on disk."""
    path = config_path()
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.debug("Failed to read AI usage prefs from %s: %s", path, exc)
        return None
    prefs = config.get("applet_prefs", {})
    return prefs.get(PREFS_KEY) or prefs.get(PREFS_KEY_LEGACY)


def replace_session(
    *,
    session_id: str,
    model_usage: dict[str, aiusage_state.ModelUsage],
) -> None:
    """Atomically replace a session's usage in Docking preferences."""
    if not model_usage:
        return

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "r") as handle:
            try:
                config = json.load(handle)
            except (json.JSONDecodeError, ValueError) as exc:
                log.debug(
                    "Failed to parse %s while updating AI usage config: %s",
                    path,
                    exc,
                )
                config = {}

        applet_prefs = config.setdefault("applet_prefs", {})
        prefs = applet_prefs.get(PREFS_KEY) or applet_prefs.get(PREFS_KEY_LEGACY, {})
        current = aiusage_state.state_from_prefs(prefs=prefs)
        updated = aiusage_state.set_session(
            state=current,
            session_id=session_id,
            model_usage=model_usage,
        )
        applet_prefs[PREFS_KEY] = aiusage_state.prefs_from_state(state=updated)

        data = json.dumps(config, indent=2) + "\n"
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, data.encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
