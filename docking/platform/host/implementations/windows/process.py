"""Headless Windows process preparation and stable installed hook invocation."""

from __future__ import annotations

import faulthandler
import subprocess
import sys
from pathlib import Path


def prepare_process() -> None:
    if sys.stderr is not None:
        faulthandler.enable()


def _hook_argv(root: Path, executable: str, *arguments: str) -> list[str]:
    from docking.platform.native.windows.installation import installed_hook

    hook = installed_hook(Path(sys.prefix))
    if hook is not None:
        return [str(hook), *arguments]
    interpreter = Path(executable)
    if interpreter.name.casefold() == "pythonw.exe":
        console = interpreter.with_name("python.exe")
        if console.is_file():
            interpreter = console
    return [
        str(interpreter),
        str(root / "docking/applets/aiusage/hook_runner.py"),
        *arguments,
    ]


def hook_argv(root: Path, executable: str, provider: str) -> list[str]:
    return _hook_argv(root, executable, provider)


def hook_command(root: Path, executable: str) -> str:
    return subprocess.list2cmdline(_hook_argv(root, executable))
