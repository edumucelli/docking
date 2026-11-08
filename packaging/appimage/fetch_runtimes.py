"""Cache pinned AppRun and AppImage runtimes through GitHub's release-asset API."""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version as installed_version
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BUILDER_VERSION = "1.1.0"
VERSION = "v2.0.0"
ASSETS = {
    "x86_64": (
        (
            f"AppRun/{VERSION}/AppRun-Release-x86_64",
            "AppImageCrafters/AppRun",
            64308783,
            "c74e7e3a085c624260f0d144c02968d00fa9383f5d7fc46b32f1dc80d371d7ff",
        ),
        (
            f"AppRun/{VERSION}/libapprun_hooks-Release-x86_64.so",
            "AppImageCrafters/AppRun",
            64308793,
            "1f64dd2161be3242f106c891b2daf27bbdd2103c20b28f57f61a56cdccb1eb6a",
        ),
        (
            "prime/runtime-x86_64",
            "AppImage/AppImageKit",
            98605527,
            "66f5b22f035022b8bdebb54c066aa6edc7b5db282fe6cdb372e7965f80772557",
        ),
    ),
    "aarch64": (
        (
            f"AppRun/{VERSION}/AppRun-Release-aarch64",
            "AppImageCrafters/AppRun",
            64308780,
            "89d289059fbb2ebf0ab84e2747c1b2fa21af935cf5c15c8daaaed207b5e513dd",
        ),
        (
            f"AppRun/{VERSION}/libapprun_hooks-Release-aarch64.so",
            "AppImageCrafters/AppRun",
            64308789,
            "ab2f3d48495b781dd1bf470192528568c46bfe8beeffb0968a0bb9674b79b74b",
        ),
        (
            "prime/runtime-aarch64",
            "AppImage/AppImageKit",
            98605521,
            "a9c09a26acb16590370e1ad0310c6f49b2677b0cecc9037fa74bdd53ec5e891f",
        ),
    ),
}


def cache_runtimes(*, arch: str, build_dir: Path) -> None:
    """Cache verified runtimes, falling back to pinned release download URLs."""
    # Match AppRunBinariesResolver and AppImagePrimer's cache layouts so the
    # builder skips browser URLs that can return persistent gateway errors.
    for name, repository, asset_id, checksum in ASSETS[arch]:
        path = build_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == checksum:
            continue
        request = Request(
            f"https://api.github.com/repos/{repository}/releases/assets/{asset_id}",
            headers={"Accept": "application/octet-stream"},
        )
        try:
            with urlopen(request, timeout=60) as response:
                content = response.read()
        except HTTPError as error:
            if error.code not in {403, 429, 500, 502, 503, 504}:
                raise
            release = (
                VERSION if repository == "AppImageCrafters/AppRun" else "continuous"
            )
            fallback = Request(
                f"https://github.com/{repository}/releases/download/{release}/{path.name}"
            )
            with urlopen(fallback, timeout=60) as response:
                content = response.read()
        if hashlib.sha256(content).hexdigest() != checksum:
            raise ValueError(f"Checksum mismatch for {name}")
        path.write_bytes(content)


def main() -> None:
    """Require the builder whose cache layout matches the pinned runtimes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arch", choices=ASSETS)
    parser.add_argument("build_dir", type=Path)
    args = parser.parse_args()
    if installed_version("appimage-builder") != BUILDER_VERSION:
        raise SystemExit(f"Install appimage-builder=={BUILDER_VERSION} before building")
    cache_runtimes(arch=args.arch, build_dir=args.build_dir)


if __name__ == "__main__":
    main()
