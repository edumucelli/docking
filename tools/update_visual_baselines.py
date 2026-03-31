"""Refresh committed screenshot baselines for visual regression tests."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/visual",
        "-q",
        "-m",
        "visual",
        "-o",
        "addopts=",
        "--update-visual-baselines",
    ]
    completed = subprocess.run(cmd, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
