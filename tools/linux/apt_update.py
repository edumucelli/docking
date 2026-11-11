"""Use Ubuntu's archive when hosted runners cannot resolve the Azure mirror."""

from __future__ import annotations

import subprocess
from pathlib import Path


def normalize_mirrors(root: Path) -> None:
    sources = (
        root / "sources.list",
        *root.glob("sources.list.d/*.list"),
        *root.glob("sources.list.d/*.sources"),
        *root.glob("apt-mirrors*.txt"),
    )
    for source in sources:
        if not source.is_file():
            continue
        original = source.read_text(encoding="utf-8")
        updated = original.replace("azure.archive.ubuntu.com", "archive.ubuntu.com")
        if updated != original:
            source.write_text(updated, encoding="utf-8")


def main() -> None:
    normalize_mirrors(Path("/etc/apt"))
    subprocess.run(["apt-get", "update"], check=True)


if __name__ == "__main__":
    main()
