#!/usr/bin/env python3
"""Validate that package-data declarations stay synchronized."""

from __future__ import annotations

import configparser
import re
import sys
from pathlib import Path

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback path
    tomllib = None


def _parse_pyproject_package_data_text(text: str) -> list[str]:
    match = re.search(
        r"(?ms)^\[tool\.setuptools\.package-data\]\s+docking\s*=\s*\[(?P<body>.*?)\]",
        text,
    )
    if match is None:
        raise KeyError("[tool.setuptools.package-data] docking")
    return re.findall(r'"([^"]+)"', match.group("body"))


def read_pyproject_package_data(root: Path) -> list[str]:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    if tomllib is not None:
        data = tomllib.loads(text)
        return list(data["tool"]["setuptools"]["package-data"]["docking"])
    return _parse_pyproject_package_data_text(text)


def read_setup_cfg_package_data(root: Path) -> list[str]:
    parser = configparser.ConfigParser()
    parser.read(root / "setup.cfg", encoding="utf-8")
    raw = parser["options.package_data"]["docking"]
    return [line.strip() for line in raw.splitlines() if line.strip()]


def read_manifest_lines(root: Path) -> set[str]:
    return {
        line.strip()
        for line in (root / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def check_package_data_sync(root: Path) -> list[str]:
    errors: list[str] = []
    pyproject = read_pyproject_package_data(root)
    setup_cfg = read_setup_cfg_package_data(root)

    if pyproject != setup_cfg:
        errors.append("pyproject.toml and setup.cfg package_data entries differ")

    manifest = read_manifest_lines(root)
    expected_manifest = {
        "recursive-include docking/assets *.svg *.png *.json *.csv.gz *.ogg *.md",
        "recursive-include docking/locale *.pot *.po *.mo",
    }
    missing = sorted(expected_manifest - manifest)
    if missing:
        errors.append(
            "MANIFEST.in is missing expected recursive includes: "
            + ", ".join(missing)
        )

    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = check_package_data_sync(root)
    if errors:
        for error in errors:
            print(f"[package-data] {error}", file=sys.stderr)
        return 1
    print("[package-data] Package data declarations are synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
