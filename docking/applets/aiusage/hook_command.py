"""Native argv for headless usage hooks, including source checkouts."""

from __future__ import annotations

import sys
from pathlib import Path


def hook_argv(*arguments: str) -> list[str]:
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
