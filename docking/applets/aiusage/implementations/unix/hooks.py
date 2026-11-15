"""Preserve Unix shell hook and legacy Codex registration policy."""

from typing import Any

from docking.applets.aiusage.hooks import UsageHooks


def _has_hook(entries: list[dict[str, Any]], needle: str) -> bool:
    for entry in entries:
        for hook in entry.get("hooks", []):
            if needle in hook.get("command", ""):
                return True
    return False


class UnixUsageHooks(UsageHooks):
    def update_claude(self, hooks: dict[str, Any]) -> bool:
        changed = False
        prefix = self._host.hook_command(self._root, self._executable)
        legacy_prefix = (
            f"PYTHONPATH={self._root} {self._executable}"
            " -m docking.applets.aiusage.hook"
        )

        # Hook groups can also contain unrelated commands.
        for event_key in ("Stop", "SessionStart"):
            entries = hooks.get(event_key, [])
            cleaned = []
            for entry in entries:
                members = []
                for hook in entry.get("hooks", []):
                    command = hook.get("command", "")
                    if "docking.applets.claude.hook" in command or (
                        legacy_prefix != prefix
                        and command.startswith(legacy_prefix + " ")
                    ):
                        continue
                    members.append(hook)
                if members == entry.get("hooks", []):
                    cleaned.append(entry)
                elif members:
                    cleaned.append({**entry, "hooks": members})
            if cleaned != entries:
                hooks[event_key] = cleaned
                changed = True

        stop_entries = hooks.get("Stop", [])
        if not _has_hook(entries=stop_entries, needle=prefix):
            stop_entries.append(
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": f"{prefix} claude Stop"}],
                }
            )
            hooks["Stop"] = stop_entries
            changed = True

        start_entries = hooks.get("SessionStart", [])
        if not _has_hook(entries=start_entries, needle=prefix):
            start_entries.append(
                {
                    "hooks": [
                        {"type": "command", "command": f"{prefix} claude SessionStart"}
                    ],
                }
            )
            hooks["SessionStart"] = start_entries
            changed = True

        return changed

    def codex_notify_is_current(self, existing: str, desired: str) -> bool:
        return existing == desired or (
            "PYTHONPATH" in existing and "docking.applets.aiusage.hook" in existing
        )
