"""Installed and source camera helper entry point, without GUI initialization."""

from __future__ import annotations

import sys
from pathlib import Path

# Preserve direct source-file invocation by the authorized pkexec command.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def main(argv: list[str] | None = None) -> int:
    from docking.platform.composition.selection import platform_provider

    return platform_provider().camera_helper(argv)


if __name__ == "__main__":
    raise SystemExit(main())
