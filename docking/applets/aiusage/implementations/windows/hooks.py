"""Windows hooks use verified native argv without a shell intermediary."""

from typing import Any

from docking.applets.aiusage.hooks import UsageHooks


def _is_owned_hook(hook: dict[str, Any]) -> bool:
    if any(
        name in hook.get("command", "")
        for name in ("docking.applets.aiusage.hook", "docking.applets.claude.hook")
    ):
        return True
    arguments = hook.get("args", [])
    return isinstance(arguments, list) and any(
        isinstance(argument, str)
        and argument.replace("\\", "/")
        .casefold()
        .endswith("/docking/applets/aiusage/hook_runner.py")
        for argument in arguments
    )


class WindowsUsageHooks(UsageHooks):
    def update_claude(self, hooks: dict[str, Any]) -> bool:
        """Use Claude's exec form so paths never pass through Bash or PowerShell."""
        changed = False
        for event in ("Stop", "SessionStart"):
            command, *arguments = [
                *self._host.hook_argv(self._root, self._executable, "claude"),
                event,
            ]
            spec = {"type": "command", "command": command, "args": arguments}
            entries = hooks.setdefault(event, [])
            if any(
                hook.get("command") == command and hook.get("args") == arguments
                for entry in entries
                for hook in entry.get("hooks", [])
            ):
                continue
            # Keep unrelated hooks, even when an old Docking hook shares their entry.
            retained = []
            for entry in entries:
                members = [
                    hook for hook in entry.get("hooks", []) if not _is_owned_hook(hook)
                ]
                if members:
                    retained.append({**entry, "hooks": members})
            retained.append(
                {"matcher": "*", "hooks": [spec]}
                if event == "Stop"
                else {"hooks": [spec]}
            )
            hooks[event] = retained
            changed = True
        return changed

    def codex_notify_is_current(self, existing: str, desired: str) -> bool:
        return existing == desired
