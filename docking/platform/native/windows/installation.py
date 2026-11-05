"""Locate stable entry points outside versioned, per-user Windows runtimes."""

from __future__ import annotations

import argparse
import configparser
import ctypes
import json
import ntpath
import os
import re
import sys
import tempfile
from pathlib import Path


def installed_hook(prefix: Path) -> Path | None:
    """Keep provider commands valid when an installed runtime is upgraded.

    An existing provider session may still use an older version's interpreter.
    Its hook command must follow the installation's current version too.
    Source checkouts and portable bundles keep their interpreter-based hooks.
    """
    version = prefix.parent
    if prefix.name != "runtime" or version.parent.name != "versions":
        return None
    root = version.parent.parent
    try:
        data = (root / "installation.ini").read_bytes()
        text = data.decode("utf-16" if data.startswith(b"\xff\xfe") else "utf-8-sig")
        marker = configparser.ConfigParser(interpolation=None)
        marker.read_string(text)
        if (
            marker.get("Docking", "Application") != "org.docking.Docking"
            or marker.get("Docking", "Schema") != "1"
        ):
            return None
    except (OSError, UnicodeError, configparser.Error):
        return None
    hook = root / "docking-hook.exe"
    return hook if hook.is_file() else None


def _same_command(command: object, helper: Path) -> bool:
    return isinstance(command, str) and ntpath.normcase(command) == ntpath.normcase(
        str(helper)
    )


def without_claude_hooks(content: bytes, helper: Path) -> bytes | None:
    """Remove this installation's commands while retaining neighboring hooks."""
    settings = json.loads(content.decode("utf-8-sig"))
    if not isinstance(settings, dict):
        raise ValueError("Invalid Claude settings")
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return None
    changed = False
    for event in ("Stop", "SessionStart"):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        retained = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                retained.append(entry)
                continue
            members = [
                item
                for item in entry["hooks"]
                if not (
                    isinstance(item, dict)
                    and _same_command(item.get("command"), helper)
                    and item.get("args") == ["claude", event]
                )
            ]
            if len(members) == len(entry["hooks"]):
                retained.append(entry)
            else:
                changed = True
                if members:
                    retained.append({**entry, "hooks": members})
        if changed:
            hooks[event] = retained
    return (
        (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        if changed
        else None
    )


def without_codex_hook(content: bytes, helper: Path) -> bytes | None:
    """Recognize the JSON argv array written by Docking's notify registration."""
    text = content.decode("utf-8-sig")
    match = re.search(
        r'^notify[ \t]*=[ \t]*(\[(?:"(?:\\.|[^"\\])*"|[\s,])*\])', text, re.MULTILINE
    )
    if match is None or re.search(r"^\[", text[: match.start()], re.MULTILINE):
        return None
    try:
        arguments = json.loads(match[1])
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(arguments, list)
        or len(arguments) != 2
        or not _same_command(arguments[0], helper)
        or arguments[1] != "codex"
    ):
        return None
    return (text[: match.start()] + text[match.end() :]).encode("utf-8")


def _replace_preferences(destination: Path, replacement: Path) -> None:
    # ReplaceFileW preserves the existing settings file's ACLs and attributes.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    replace.restype = ctypes.c_int32
    if not replace(str(destination), str(replacement), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def remove_provider_hooks(root: Path, home: Path) -> None:
    """Unregister owned usage hooks before the installer removes their runtime."""
    helper = root / "docking-hook.exe"
    changes: list[tuple[Path, bytes, bytes]] = []
    for path, transform in (
        (home / ".claude/settings.json", without_claude_hooks),
        (home / ".codex/config.toml", without_codex_hook),
    ):
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            continue
        updated = transform(content, helper)
        if updated is not None:
            changes.append((path, content, updated))
    # Parse both files before changing either. No other provider settings move.
    for path, original, updated in changes:
        descriptor, filename = tempfile.mkstemp(
            prefix=".docking-uninstall-", dir=path.parent
        )
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            if path.read_bytes() != original:
                raise OSError("Provider settings changed during uninstall")
            _replace_preferences(path, temporary)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove-hooks", type=Path, required=True)
    args = parser.parse_args()
    if (
        sys.platform != "win32"
        or installed_hook(Path(sys.prefix)) != args.remove_hooks / "docking-hook.exe"
    ):
        parser.error("Use this installation's bundled Windows interpreter")
    try:
        remove_provider_hooks(args.remove_hooks, Path.home())
    except (OSError, ValueError):
        raise SystemExit("Could not remove Docking's provider hooks") from None


if __name__ == "__main__":
    main()
