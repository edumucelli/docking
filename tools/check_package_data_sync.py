#!/usr/bin/env python3
"""Validate package-data includes for the source distribution."""

from __future__ import annotations

import sys
from pathlib import Path


def read_manifest_lines(root: Path) -> set[str]:
    return {
        line.strip()
        for line in (root / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def check_package_data_sync(root: Path) -> list[str]:
    errors: list[str] = []
    manifest = read_manifest_lines(root)
    expected_manifest = {
        "recursive-include docking/assets *.svg *.png *.json *.csv.gz *.ogg *.md",
        "recursive-include docking/locale *.pot *.po *.mo",
    }
    missing = sorted(expected_manifest - manifest)
    if missing:
        errors.append(
            "MANIFEST.in is missing expected recursive includes: " + ", ".join(missing)
        )

    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = check_package_data_sync(root)
    if errors:
        for error in errors:
            print(f"[package-data] {error}", file=sys.stderr)
        return 1
    print("[package-data] Source distribution includes package data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
