#!/usr/bin/env python3
"""Validate translation compilation and packaging expectations."""

from __future__ import annotations

import configparser
import sys
from pathlib import Path

import tomllib


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _has_mo_package_data(root: Path) -> bool:
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_data = pyproject["tool"]["setuptools"]["package-data"]["docking"]

    parser = configparser.ConfigParser()
    parser.read(root / "setup.cfg", encoding="utf-8")
    setup_cfg_data = [
        line.strip()
        for line in parser["options.package_data"]["docking"].splitlines()
        if line.strip()
    ]

    expected = "locale/*/LC_MESSAGES/*.mo"
    return expected in pyproject_data and expected in setup_cfg_data


def check_translation_packaging(root: Path) -> list[str]:
    errors: list[str] = []

    if not _has_mo_package_data(root):
        errors.append("Compiled .mo catalogs are not declared in package_data")

    expected_compile_paths = {
        "packaging/deb/debian/rules": "bash tools/i18n.sh --compile",
        "packaging/rpm/docking.spec": "bash tools/i18n.sh --compile",
        "packaging/flatpak/org.docking.Docking.json": "bash tools/i18n.sh --compile",
        "packaging/snap/snapcraft.yaml": (
            'bash "$CRAFT_PART_SRC/tools/i18n.sh" --compile'
        ),
        "packaging/appimage/build.sh": 'bash "${ROOT_DIR}/tools/i18n.sh" --compile',
        "packaging/arch/build.sh": 'bash "${ROOT_DIR}/tools/i18n.sh" --compile',
        "packaging/nix/build.sh": 'bash "${ROOT_DIR}/tools/i18n.sh" --compile',
        ".github/workflows/ci.yml": "bash tools/i18n.sh --compile",
    }

    for rel_path, needle in expected_compile_paths.items():
        text = _read(root / rel_path)
        if needle not in text:
            errors.append(f"{rel_path} is missing translation compile step")

    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = check_translation_packaging(root)
    if errors:
        for error in errors:
            print(f"[i18n-packaging] {error}", file=sys.stderr)
        return 1
    print("[i18n-packaging] Translation packaging expectations are satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
