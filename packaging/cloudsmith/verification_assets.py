"""Download a stable release and the preceding release for native repository checks."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def version(tag: str) -> tuple[int, ...]:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError(f"Expected a stable vX.Y.Z tag: {tag}")
    return tuple(int(part) for part in tag[1:].split("."))


def gh_json(*args: str):
    return json.loads(subprocess.check_output(["gh", *args], text=True))


def download(tag: str, asset: str, directory: Path) -> None:
    subprocess.run(
        ["gh", "release", "download", tag, "--pattern", asset, "--dir", str(directory)],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    parser.add_argument("--format", choices=("deb", "rpm"), default="deb")
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    current = version(args.release_tag)
    release = gh_json(
        "release", "view", args.release_tag, "--json", "isDraft,isPrerelease"
    )
    if release["isDraft"] or release["isPrerelease"]:
        raise SystemExit("Repository verification requires a published stable release")
    suffix = "x86_64" if args.arch == "amd64" else "aarch64"
    asset = f"docking-{args.release_tag[1:]}-linux-{suffix}.{args.format}"
    download(args.release_tag, asset, args.directory / "current")
    releases = gh_json(
        "release", "list", "--limit", "100", "--json", "tagName,isDraft,isPrerelease"
    )
    previous = [
        item["tagName"]
        for item in releases
        if not item["isDraft"]
        and not item["isPrerelease"]
        and re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", item["tagName"])
        and version(item["tagName"]) < current
    ]
    if previous:
        tag = max(previous, key=version)
        preceding = f"docking-{tag[1:]}-linux-{suffix}.{args.format}"
        metadata = gh_json("release", "view", tag, "--json", "assets")
        if any(item["name"] == preceding for item in metadata["assets"]):
            download(tag, preceding, args.directory / "previous")
            return
        if not (args.arch == "arm64" and current == (2, 13, 8)):
            raise SystemExit(
                f"Missing preceding stable asset {preceding}; cannot verify upgrade"
            )
    # ARM64 was introduced in 2.13.8; its first publication has no predecessor.
    print(f"::notice::No preceding stable {args.arch} package; fresh install only")


if __name__ == "__main__":
    main()
