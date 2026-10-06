"""Validate and publish a release pair without replacing existing package bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Format:
    """Per-format rules shared by validation, remote matching, and upload."""

    name: str
    extension: str
    architectures: tuple[str, ...]
    # Cloudsmith GPG-signs RPMs on upload, which regenerates the checksum. The
    # published bytes therefore never match the uploaded file, so identity has
    # to rest on the package name and version instead of a checksum.
    resigns_on_upload: bool
    fields: Callable[[Path], tuple[str, str, str, str]]
    push_arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class Package:
    path: Path
    version: str
    architecture: str
    sha256: str
    release: str = ""

    def matches_remote(self, item: dict) -> bool:
        """Whether a Cloudsmith record refers to exactly this package."""
        remote = item.get("version")
        combined = f"{self.version}-{self.release}" if self.release else self.version
        if remote == combined:
            return True
        if not self.release or remote != self.version:
            return False
        # Cloudsmith may report an RPM with the release folded into the version
        # or kept separate. A bare version is ambiguous across releases, so the
        # release field has to agree whenever one is reported.
        remote_release = item.get("release")
        return remote_release is None or remote_release == self.release


def deb_fields(path: Path) -> tuple[str, str, str, str]:
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
    return name, package_version, architecture, ""


def rpm_fields(path: Path) -> tuple[str, str, str, str]:
    fields = subprocess.check_output(
        [
            "rpm",
            "-qp",
            "--queryformat",
            "%{NAME}\n%{VERSION}\n%{RELEASE}\n%{ARCH}",
            str(path),
        ],
        text=True,
    ).splitlines()
    name, package_version, release, architecture = fields
    return name, package_version, architecture, release


FORMATS = {
    "deb": Format(
        name="deb",
        extension=".deb",
        architectures=("amd64", "arm64"),
        resigns_on_upload=False,
        fields=deb_fields,
        push_arguments=("--component", "main"),
    ),
    "rpm": Format(
        name="rpm",
        extension=".rpm",
        architectures=("x86_64", "aarch64"),
        resigns_on_upload=True,
        fields=rpm_fields,
    ),
}


def release_packages(
    directory: Path, version: str, fmt: Format = FORMATS["deb"]
) -> list[Package]:
    """Preflight the entire release before any upload can happen."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a stable X.Y.Z project version")
    packages = []
    for path in sorted(directory.glob(f"*{fmt.extension}")):
        name, package_version, architecture, release = fmt.fields(path)
        if name != "docking" or architecture not in fmt.architectures:
            raise ValueError(
                f"Unexpected package: {path.name}: "
                f"{name} {package_version} {architecture} {release}".rstrip()
            )
        if fmt.name == "deb":
            expected = re.escape(version) + r"-\d+"
            label = f"{package_version}"
        else:
            expected = re.escape(version)
            label = f"{package_version}-{release}"
        if not re.fullmatch(expected, package_version) or (
            fmt.name == "rpm" and not re.fullmatch(r"\d+", release)
        ):
            raise ValueError(
                f"Package version {label} does not match release {version}"
            )
        packages.append(
            Package(
                path,
                package_version,
                architecture,
                hashlib.sha256(path.read_bytes()).hexdigest(),
                release,
            )
        )
    expected_architectures = sorted(fmt.architectures)
    if sorted(package.architecture for package in packages) != expected_architectures:
        raise ValueError(
            "Expected exactly one docking package for each of "
            + " and ".join(expected_architectures)
        )
    if len({(package.version, package.release) for package in packages}) != 1:
        detail = (
            "RPM version and release" if fmt.name == "rpm" else "Debian package version"
        )
        raise ValueError(f"Both architectures must have the same {detail}")
    return packages


def remote_packages(repository: str, fmt: Format = FORMATS["deb"]) -> list[dict]:
    result = subprocess.check_output(
        [
            "cloudsmith",
            "list",
            "packages",
            repository,
            "--query",
            f"format:{fmt.name} name:^docking$",
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


def existing_package(
    package: Package, remote: list[dict], fmt: Format = FORMATS["deb"]
) -> dict | None:
    matches = [
        item
        for item in remote
        if item.get("format") == fmt.name
        and item.get("name") == "docking"
        and package.matches_remote(item)
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
    if not fmt.resigns_on_upload and item.get("checksum_sha256") != package.sha256:
        raise ValueError(
            f"Conflicting published bytes: {package.version}/{package.architecture}; "
            f"bump the {fmt.name} revision"
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


def publish(
    repository: str,
    packages: list[Package],
    *,
    fmt: Format = FORMATS["deb"],
    timeout: float = 300,
) -> None:
    if not re.fullmatch(r"[a-z0-9_-]+/[a-z0-9_-]+", repository):
        raise ValueError(
            "Repository must be WORKSPACE/REPOSITORY using Cloudsmith slugs"
        )
    remote = remote_packages(repository, fmt)
    # Check both architectures for conflicting bytes before uploading either one.
    existing = [existing_package(package, remote, fmt) for package in packages]
    for package, item in zip(packages, existing, strict=True):
        if item is None:
            subprocess.run(
                [
                    "cloudsmith",
                    "push",
                    fmt.name,
                    f"{repository}/any-distro/any-version",
                    str(package.path),
                    *fmt.push_arguments,
                ],
                check=True,
            )
        else:
            detail = (
                "re-signed on upload" if fmt.resigns_on_upload else "matching SHA-256"
            )
            print(
                f"Already published ({detail}): "
                f"{package.architecture} {package.version}"
            )
    deadline = time.monotonic() + timeout
    while True:
        remote = remote_packages(repository, fmt)
        items = [existing_package(package, remote, fmt) for package in packages]
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
    parser.add_argument("--format", choices=sorted(FORMATS), default="deb")
    parser.add_argument("--repository")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    fmt = FORMATS[args.format]
    packages = release_packages(args.directory, args.version, fmt)
    for package in packages:
        print(
            f"Validated {package.path.name}: "
            f"{'-'.join(filter(None, (package.version, package.release)))} "
            f"{package.architecture} {package.sha256}"
        )
    if not args.validate_only:
        if not args.repository:
            parser.error("--repository is required for publication")
        publish(args.repository, packages, fmt=fmt)


if __name__ == "__main__":
    main()
