"""Stage the VM-verified BSD packages and checksums for GitHub Releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

TARGETS = {("freebsd", "15.1", "amd64"): ".pkg", ("openbsd", "7.9", "amd64"): ".tgz"}


def stage(directories: list[Path], version: str, destination: Path) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a stable X.Y.Z project version")
    packages = []
    seen = set()
    for directory in directories:
        for manifest in sorted(directory.glob("docking-*.json")):
            data = json.loads(manifest.read_text())
            target = data["os"], data["release"], data["architecture"]
            if target not in TARGETS or target in seen:
                raise ValueError(f"Unexpected or duplicate BSD target: {manifest.name}")
            if data["name"] != "docking" or data["version"] != version:
                raise ValueError(f"BSD package does not match release: {manifest.name}")
            seen.add(target)
            os_name, release, architecture = target
            suffix = TARGETS[target]
            name = f"docking-{version}-{os_name}-{release}-{architecture}{suffix}"
            if data["filename"] != name or manifest.name != name + ".json":
                raise ValueError(f"Unexpected BSD filename: {manifest.name}")
            package = directory / name
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            if digest != data["sha256"]:
                raise ValueError(f"BSD package checksum mismatch: {name}")
            alias = f"docking-latest-{os_name}-{release}-{architecture}{suffix}"
            packages.append((package, manifest, alias))
    if seen != set(TARGETS):
        raise ValueError("Expected one VM-verified package for each BSD target")
    destination.mkdir(parents=True, exist_ok=True)
    checksums = []
    for package, manifest, alias in packages:
        for source, name in (
            (package, package.name),
            (manifest, manifest.name),
            (package, alias),
            (manifest, alias + ".json"),
        ):
            target = destination / name
            shutil.copy2(source, target)
            checksums.append(
                f"{hashlib.sha256(target.read_bytes()).hexdigest()}  {name}"
            )
    (destination / "SHA256SUMS-bsd").write_text("\n".join(sorted(checksums)) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, action="append", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage(args.directory, args.version, args.output)


if __name__ == "__main__":
    main()
