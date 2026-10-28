"""Stage Windows packages accepted by the native CI job for GitHub Releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

REQUIRED_CHECKS = (
    "in_use_upgrade_rejected",
    "in_use_uninstall_rejected",
    "upgrade_preserved_startup_and_previous_payload",
    "startup_disabled",
    "installed_desktop",
    "uninstall_preserved_preferences_and_unknown_files",
)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def stage(directory: Path, version: str, destination: Path) -> None:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a stable X.Y.Z project version")
    packages: list[tuple[Path, str, str]] = []
    try:
        for suffix in (".zip", "-setup.exe"):
            package = directory / "dist" / ("docking-windows-x86_64" + suffix)
            checksum = digest(package)
            companion = package.with_suffix(package.suffix + ".sha256")
            if companion.read_text(encoding="utf-8").strip() != (
                checksum + "  " + package.name
            ):
                raise ValueError(f"Windows package checksum mismatch: {package.name}")
            packages.append((package, suffix, checksum))
        with zipfile.ZipFile(packages[0][0]) as archive:
            metadata = json.loads(
                archive.read("Docking/runtime/share/docking/runtime.json")
            )
            inventory = json.loads(archive.read("Docking/files.sha256.json"))
        report = json.loads(
            (directory / "artifacts/windows/installer/result.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ValueError("Incomplete Windows release artifacts") from exc
    if not isinstance(metadata, dict) or metadata.get("version") != version:
        raise ValueError("Windows package does not match the release version")
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError("Windows package lacks its file inventory")
    if (
        not isinstance(report, dict)
        or "error" in report
        or report.get("installer_sha256") != packages[1][2]
        or any(report.get(check) is not True for check in REQUIRED_CHECKS)
        or any(
            type(report.get(check)) is not int or report[check] != len(inventory)
            for check in ("verified_payload_files", "verified_final_payload_files")
        )
    ):
        raise ValueError("Windows packages lack matching installer acceptance")
    # Preflight every input before adding anything to the shared release folder.
    destination.mkdir(parents=True, exist_ok=True)
    checksums = []
    for package, suffix, checksum in packages:
        for label in (version, "latest"):
            name = f"docking-{label}-windows-x86_64{suffix}"
            shutil.copy2(package, destination / name)
            checksums.append(f"{checksum}  {name}")
    (destination / "SHA256SUMS-windows").write_text(
        "\n".join(sorted(checksums)) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage(args.directory, args.version, args.output)


if __name__ == "__main__":
    main()
