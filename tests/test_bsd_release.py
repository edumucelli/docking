"""Only matching VM-verified BSD artifacts may enter a GitHub release."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "packaging/bsd/release.py"
spec = importlib.util.spec_from_file_location("docking_bsd_release", MODULE_PATH)
assert spec and spec.loader
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def incoming(tmp_path):
    for (os_name, os_release, architecture), suffix in release.TARGETS.items():
        name = f"docking-1.2.3-{os_name}-{os_release}-{architecture}{suffix}"
        package = tmp_path / name
        package.write_bytes(f"VM-tested {os_name} payload".encode())
        package.with_suffix(suffix + ".json").write_text(
            json.dumps(
                {
                    "name": "docking",
                    "version": "1.2.3",
                    "os": os_name,
                    "release": os_release,
                    "architecture": architecture,
                    "filename": name,
                    "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                }
            )
        )
    return tmp_path


def test_versions_aliases_and_checksums_retain_original_bytes(incoming, tmp_path):
    destination = tmp_path / "release"
    release.stage([incoming], "1.2.3", destination)
    assert len(list(destination.iterdir())) == 9
    for line in (destination / "SHA256SUMS-bsd").read_text().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest
    for (os_name, os_release, architecture), suffix in release.TARGETS.items():
        assert (
            destination / f"docking-1.2.3-{os_name}-{os_release}-{architecture}{suffix}"
        ).read_bytes() == (
            destination
            / f"docking-latest-{os_name}-{os_release}-{architecture}{suffix}"
        ).read_bytes()


@pytest.mark.parametrize("defect", ["checksum", "version", "missing", "duplicate"])
def test_preflight_rejects_invalid_release_without_staging(incoming, tmp_path, defect):
    directories = [incoming]
    version = "1.2.3"
    if defect == "checksum":
        next(incoming.glob("*.pkg")).write_bytes(b"changed")
    elif defect == "version":
        version = "9.9.9"
    elif defect == "missing":
        next(incoming.glob("*.tgz.json")).unlink()
    else:
        directories.append(incoming)
    destination = tmp_path / "release"
    with pytest.raises(ValueError):
        release.stage(directories, version, destination)
    assert not destination.exists()
