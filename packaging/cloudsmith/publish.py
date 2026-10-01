"""Validate and publish a release pair without replacing existing package bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Package:
    path: Path
    version: str
    architecture: str
    sha256: str


def release_packages(directory: Path, version: str) -> list[Package]:
    """Preflight the entire release before any upload can happen."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a stable X.Y.Z project version")
    packages = []
    for path in sorted(directory.glob("*.deb")):
        fields = subprocess.check_output(
            [
                "dpkg-deb",
                "--show",
                "--showformat=${Package}\n${Version}\n${Architecture}",
                str(path),
            ],
            text=True,
        ).splitlines()
        name, package_version, architecture = fields
        if name != "docking" or architecture not in {"amd64", "arm64"}:
            raise ValueError(f"Unexpected package: {path.name}: {fields}")
        if not re.fullmatch(re.escape(version) + r"-\d+", package_version):
            raise ValueError(
                f"Package version {package_version} does not match release {version}"
            )
        packages.append(
            Package(
                path,
                package_version,
                architecture,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    if sorted(package.architecture for package in packages) != ["amd64", "arm64"]:
        raise ValueError(
            "Expected exactly one docking package for each of amd64 and arm64"
        )
    if len({package.version for package in packages}) != 1:
        raise ValueError("Both architectures must have the same Debian package version")
    return packages


def remote_packages(repository: str) -> list[dict]:
    result = subprocess.check_output(
        [
            "cloudsmith",
            "list",
            "packages",
            repository,
            "--query",
            "format:deb name:^docking$",
            "--page-all",
            "--output-format",
            "json",
        ],
        text=True,
    )
    data = json.loads(result)["data"]
    if not isinstance(data, list):
        raise ValueError("Unexpected Cloudsmith package list response")
    return data


def existing_package(package: Package, remote: list[dict]) -> dict | None:
    matches = [
        item
        for item in remote
        if item.get("format") == "deb"
        and item.get("name") == "docking"
        and item.get("version") == package.version
        and any(
            arch.get("name") == package.architecture
            for arch in item.get("architectures", [])
        )
    ]
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous remote package: {package.version}/{package.architecture}"
        )
    if not matches:
        return None
    item = matches[0]
    if item.get("checksum_sha256") != package.sha256:
        raise ValueError(
            f"Conflicting published bytes: {package.version}/{package.architecture}; "
            "bump the Debian revision"
        )
    if (item.get("distro") or {}).get("slug") != "any-distro" or (
        item.get("distro_version") or {}
    ).get("slug") != "any-version":
        raise ValueError(
            "Existing package is outside the shared any-distro/any-version target"
        )
    if (
        item.get("is_sync_failed")
        or item.get("is_quarantined")
        or item.get("is_hidden")
    ):
        raise ValueError(
            f"Remote package is failed, quarantined, or hidden: {package.architecture}"
        )
    return item


def publish(repository: str, packages: list[Package], *, timeout: float = 300) -> None:
    if not re.fullmatch(r"[a-z0-9_-]+/[a-z0-9_-]+", repository):
        raise ValueError(
            "Repository must be WORKSPACE/REPOSITORY using Cloudsmith slugs"
        )
    remote = remote_packages(repository)
    # Check both architectures for conflicting bytes before uploading either one.
    existing = [existing_package(package, remote) for package in packages]
    for package, item in zip(packages, existing, strict=True):
        if item is None:
            subprocess.run(
                [
                    "cloudsmith",
                    "push",
                    "deb",
                    f"{repository}/any-distro/any-version",
                    str(package.path),
                    "--component",
                    "main",
                ],
                check=True,
            )
        else:
            print(
                "Already uploaded with matching SHA-256: "
                f"{package.architecture} {package.version}"
            )
    deadline = time.monotonic() + timeout
    while True:
        remote = remote_packages(repository)
        items = [existing_package(package, remote) for package in packages]
        if all(
            item
            and item.get("is_sync_completed")
            and item.get("is_downloadable")
            and item.get("indexed")
            for item in items
        ):
            print(f"Published and indexed both architectures: {packages[0].version}")
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Cloudsmith did not index both packages within the verification window"
            )
        time.sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    packages = release_packages(args.directory, args.version)
    for package in packages:
        print(
            f"Validated {package.path.name}: {package.version} "
            f"{package.architecture} {package.sha256}"
        )
    if not args.validate_only:
        if not args.repository:
            parser.error("--repository is required for publication")
        publish(args.repository, packages)


if __name__ == "__main__":
    main()
