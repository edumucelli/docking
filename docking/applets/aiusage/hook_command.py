"""Native argv for headless usage hooks, including source checkouts."""

from __future__ import annotations

import sys
from pathlib import Path

from docking.platform.environment import is_windows


def hook_argv(*arguments: str) -> list[str]:
    if is_windows():
        from docking.platform.backends.windows.installation import installed_hook

        hook = installed_hook(Path(sys.prefix))
        if hook is not None:
            return [str(hook), *arguments]
    interpreter = Path(sys.executable)
    if interpreter.name.casefold() == "pythonw.exe":
        console = interpreter.with_name("python.exe")
        if console.is_file():
            interpreter = console
    return [
        str(interpreter),
        str(Path(__file__).with_name("hook_runner.py")),
        *arguments,
    ]
