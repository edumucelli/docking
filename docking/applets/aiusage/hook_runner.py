"""Bootstrap a headless hook without shell-specific PYTHONPATH syntax."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.applets.aiusage.hook import main as run_hook

    run_hook()


if __name__ == "__main__":
    main()
