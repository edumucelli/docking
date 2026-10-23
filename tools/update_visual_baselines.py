"""Refresh committed screenshot baselines for visual regression tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def main() -> int:
    xvfb_run = shutil.which("xvfb-run")
    if xvfb_run is None:
        print(
            "xvfb-run is required to refresh visual baselines in a headless GTK "
            "environment. Install xvfb and retry.",
            file=sys.stderr,
        )
        return 1

    cmd = [
        xvfb_run,
        "-a",
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
    env = os.environ.copy()
    env.setdefault("GSETTINGS_BACKEND", "memory")
    env.setdefault("GTK_THEME", "Adwaita")
    completed = subprocess.run(cmd, check=False, env=env)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
