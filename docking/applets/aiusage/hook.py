"""CLI entry point for AI usage hooks.

Claude Code invocation:
    python3 -m docking.applets.aiusage.hook claude Stop  (JSON via stdin)
    python3 -m docking.applets.aiusage.hook claude SessionStart

Codex CLI invocation (via notify):
    python3 -m docking.applets.aiusage.hook codex <json_arg>

No GTK imports -- this runs as a standalone subprocess.
"""

from __future__ import annotations

import json
import sys

from docking.applets.aiusage.backends import backend_for_name
from docking.log import get_logger

log = get_logger("aiusage.hook")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main() -> None:
    """Dispatch based on provider (argv[1]) and event."""
    if len(sys.argv) < 2:
        return

    provider = sys.argv[1]
    backend = backend_for_name(provider)
    if backend is None:
        return

    if provider == "claude":
        event = sys.argv[2] if len(sys.argv) > 2 else ""
        try:
            data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError) as exc:
            log.debug("Failed to parse Claude hook stdin payload: %s", exc)
            return
        backend.handle_hook(event=event, payload=data)

    elif provider == "codex":
        # Codex appends the JSON payload as the last CLI arg.
        json_arg = sys.argv[-1] if len(sys.argv) > 2 else "{}"
        backend.handle_hook(event="", payload=json_arg)


if __name__ == "__main__":
    main()
