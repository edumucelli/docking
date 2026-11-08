"""Cache the pinned AppRun runtime through GitHub's release-asset API."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from urllib.request import Request, urlopen

VERSION = "v2.0.0"
ASSETS = {
    "x86_64": (
        (
            "AppRun-Release-x86_64",
            64308783,
            "c74e7e3a085c624260f0d144c02968d00fa9383f5d7fc46b32f1dc80d371d7ff",
        ),
        (
            "libapprun_hooks-Release-x86_64.so",
            64308793,
            "1f64dd2161be3242f106c891b2daf27bbdd2103c20b28f57f61a56cdccb1eb6a",
        ),
    ),
    "aarch64": (
        (
            "AppRun-Release-aarch64",
            64308780,
            "89d289059fbb2ebf0ab84e2747c1b2fa21af935cf5c15c8daaaed207b5e513dd",
        ),
        (
            "libapprun_hooks-Release-aarch64.so",
            64308789,
            "ab2f3d48495b781dd1bf470192528568c46bfe8beeffb0968a0bb9674b79b74b",
        ),
    ),
}


def cache_apprun(*, arch: str, build_dir: Path) -> None:
    # Match appimage-builder's AppRunBinariesResolver cache layout so it skips
    # the browser download URLs, which can return persistent gateway errors.
    cache_dir = build_dir / "AppRun" / VERSION
    cache_dir.mkdir(parents=True, exist_ok=True)
    for name, asset_id, checksum in ASSETS[arch]:
        path = cache_dir / name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == checksum:
            continue
        request = Request(
            f"https://api.github.com/repos/AppImageCrafters/AppRun/releases/assets/{asset_id}",
            headers={"Accept": "application/octet-stream"},
        )
        with urlopen(request, timeout=60) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != checksum:
            raise ValueError(f"Checksum mismatch for {name}")
        path.write_bytes(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arch", choices=ASSETS)
    parser.add_argument("build_dir", type=Path)
    args = parser.parse_args()
    cache_apprun(arch=args.arch, build_dir=args.build_dir)


if __name__ == "__main__":
    main()
