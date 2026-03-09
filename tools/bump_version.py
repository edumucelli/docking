#!/usr/bin/env python3
"""Bump Docking version consistently across packaging and metadata files.

This script is intentionally explicit rather than clever. Docking currently
ships version information in several formats with different syntax:

- TOML (`pyproject.toml`)
- INI-like metadata (`setup.cfg`)
- Python source (`docking/__init__.py`)
- RPM spec
- Snap manifest
- Arch PKGBUILD
- Nix derivation
- Debian changelog
- AppStream/metainfo release list

The goal is not to automate an entire release pipeline. The goal is to make the
existing release bookkeeping deterministic and keep the repo from drifting.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ReplacementTarget:
    path: Path
    pattern: str
    replacement: str
    count: int = 1


def _replace_once(target: ReplacementTarget, *, version: str) -> None:
    text = target.path.read_text(encoding="utf-8")
    replacement = target.replacement.format(version=version)
    updated, changed = re.subn(target.pattern, replacement, text, count=target.count)
    if changed != target.count:
        msg = f"Expected {target.count} replacement(s) in {target.path}, got {changed}"
        raise RuntimeError(msg)
    target.path.write_text(updated, encoding="utf-8")


def _prepend_debian_changelog(*, version: str) -> None:
    path = ROOT / "packaging/deb/debian/changelog"
    text = path.read_text(encoding="utf-8")
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if first_line.startswith(f"docking ({version}-1) "):
        return

    timestamp = format_datetime(datetime.now().astimezone())
    entry = (
        f"docking ({version}-1) unstable; urgency=medium\n\n"
        f"  * Release {version}.\n\n"
        f" -- Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com>  {timestamp}\n\n"
    )
    path.write_text(entry + text, encoding="utf-8")


def _prepend_rpm_changelog(*, version: str) -> None:
    path = ROOT / "packaging/rpm/docking.spec"
    text = path.read_text(encoding="utf-8")
    marker = "%changelog\n"
    if marker not in text:
        raise RuntimeError(f"Missing %changelog section in {path}")

    head, tail = text.split(marker, 1)
    if re.match(
        rf"\* .+ - {re.escape(version)}-1\n- Release {re.escape(version)}\.\n",
        tail,
    ):
        return
    today = datetime.now().astimezone()
    maintainer = "Eduardo Mucelli Rezende Oliveira <edumucelli@gmail.com>"
    first_line = f"* {today:%a %b %d %Y} {maintainer} - {version}-1\n"

    entry = first_line + f"- Release {version}.\n\n"
    path.write_text(head + marker + entry + tail, encoding="utf-8")


def _upsert_metainfo_release(*, version: str) -> None:
    path = ROOT / "packaging/flatpak/org.docking.Docking.metainfo.xml"
    text = path.read_text(encoding="utf-8")
    if re.search(rf'<release version="{re.escape(version)}" date="[^"]+" />', text):
        return
    date = datetime.now().date().isoformat()
    entry = f'    <release version="{version}" date="{date}" />\n'

    marker = "  <releases>\n"
    if marker not in text:
        raise RuntimeError(f"Missing <releases> section in {path}")
    text = text.replace(marker, marker + entry, 1)
    path.write_text(text, encoding="utf-8")


def bump_version(*, version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Version must match MAJOR.MINOR.PATCH")

    replacements = [
        ReplacementTarget(
            path=ROOT / "pyproject.toml",
            pattern=r'(?m)^version = "\d+\.\d+\.\d+"$',
            replacement='version = "{version}"',
        ),
        ReplacementTarget(
            path=ROOT / "setup.cfg",
            pattern=r"(?m)^version = \d+\.\d+\.\d+$",
            replacement="version = {version}",
        ),
        ReplacementTarget(
            path=ROOT / "docking/__init__.py",
            pattern=r'(?m)^__version__ = "\d+\.\d+\.\d+"$',
            replacement='__version__ = "{version}"',
        ),
        ReplacementTarget(
            path=ROOT / "packaging/rpm/docking.spec",
            pattern=r"(?m)^Version:\s+%\{\?pkg_version\}%\{!\?pkg_version:\d+\.\d+\.\d+\}$",
            replacement="Version:        %{{?pkg_version}}%{{!?pkg_version:{version}}}",
        ),
        ReplacementTarget(
            path=ROOT / "packaging/snap/snapcraft.yaml",
            pattern=r'(?m)^version: "\d+\.\d+\.\d+"$',
            replacement='version: "{version}"',
        ),
        ReplacementTarget(
            path=ROOT / "packaging/arch/PKGBUILD",
            pattern=r"(?m)^pkgver=\d+\.\d+\.\d+$",
            replacement="pkgver={version}",
        ),
        ReplacementTarget(
            path=ROOT / "packaging/nix/default.nix",
            pattern=r'(?m)^  version = "\d+\.\d+\.\d+";$',
            replacement='  version = "{version}";',
        ),
    ]

    for target in replacements:
        _replace_once(target, version=version)

    _prepend_debian_changelog(version=version)
    _prepend_rpm_changelog(version=version)
    _upsert_metainfo_release(version=version)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="New version in MAJOR.MINOR.PATCH format")
    args = parser.parse_args()
    bump_version(version=args.version)
    print(f"Updated Docking version surfaces to {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
