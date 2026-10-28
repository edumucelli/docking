"""Release staging binds Windows packages to the native acceptance artifact."""

import hashlib
import json
import zipfile

import pytest

from tools.windows import release


def checksum_file(path):
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n",
        encoding="utf-8",
    )


def write_archive(path, *, version="1.2.3", inventory=None):
    files = {
        "Docking.exe": b"portable unit fixture",
        "runtime/share/docking/runtime.json": json.dumps({"version": version}).encode(),
    }
    if inventory is None:
        inventory = {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        }
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr("Docking/" + name, data)
        archive.writestr("Docking/files.sha256.json", json.dumps(inventory))
    checksum_file(path)


@pytest.fixture
def incoming(tmp_path):
    directory = tmp_path / "incoming"
    packages = directory / "dist"
    packages.mkdir(parents=True)
    write_archive(packages / "docking-windows-x86_64.zip")
    setup = packages / "docking-windows-x86_64-setup.exe"
    setup.write_bytes(b"installer unit fixture")
    checksum_file(setup)
    report = directory / "artifacts/windows/installer/result.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(
            {
                "installer_sha256": hashlib.sha256(setup.read_bytes()).hexdigest(),
                "verified_payload_files": 2,
                "in_use_upgrade_rejected": True,
                "in_use_uninstall_rejected": True,
                "upgrade_preserved_startup_and_previous_payload": True,
                "startup_disabled": True,
                "verified_final_payload_files": 2,
                "installed_desktop": True,
                "uninstall_preserved_preferences_and_unknown_files": True,
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_release_versions_aliases_and_checksums_retain_accepted_bytes(
    incoming, tmp_path
):
    destination = tmp_path / "release"
    destination.mkdir()
    other_platform = destination / "existing-bsd.pkg"
    other_platform.write_bytes(b"retain another platform")
    release.stage(incoming, "1.2.3", destination)
    assert other_platform.read_bytes() == b"retain another platform"
    sums = (destination / "SHA256SUMS-windows").read_text().splitlines()
    assert len(sums) == 4
    for line in sums:
        digest, name = line.split("  ")
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest
    for suffix in (".zip", "-setup.exe"):
        assert (
            destination / ("docking-1.2.3-windows-x86_64" + suffix)
        ).read_bytes() == (
            incoming / "dist" / ("docking-windows-x86_64" + suffix)
        ).read_bytes()
        assert (
            destination / ("docking-latest-windows-x86_64" + suffix)
        ).read_bytes() == (
            destination / ("docking-1.2.3-windows-x86_64" + suffix)
        ).read_bytes()
    assert not list(destination.glob("*.json"))


@pytest.mark.parametrize(
    "defect",
    [
        "zip-checksum",
        "setup-checksum",
        "companion-filename",
        "version",
        "missing-package",
        "missing-report",
        "different-setup",
        "empty-inventory",
        "initial-count",
        "final-count",
        "error-report",
        "invalid-json",
    ],
)
def test_invalid_or_unaccepted_packages_do_not_enter_the_release(
    incoming, tmp_path, defect
):
    archive = incoming / "dist/docking-windows-x86_64.zip"
    setup = incoming / "dist/docking-windows-x86_64-setup.exe"
    report = incoming / "artifacts/windows/installer/result.json"
    data = json.loads(report.read_text())
    if defect == "zip-checksum":
        archive.write_bytes(archive.read_bytes() + b"changed")
    elif defect == "setup-checksum":
        setup.write_bytes(b"changed")
    elif defect == "companion-filename":
        setup.with_suffix(".exe.sha256").write_text(
            hashlib.sha256(setup.read_bytes()).hexdigest() + "  different.exe\n"
        )
    elif defect == "version":
        write_archive(archive, version="9.9.9")
    elif defect == "missing-package":
        setup.unlink()
    elif defect == "empty-inventory":
        write_archive(archive, inventory={})
    else:
        if defect == "different-setup":
            data["installer_sha256"] = "0" * 64
        elif defect == "initial-count":
            data["verified_payload_files"] = 1
        elif defect == "final-count":
            data["verified_final_payload_files"] = 1
        elif defect == "error-report":
            data["error"] = "Uninstall failed"
        report.write_text(json.dumps(data), encoding="utf-8")
        if defect == "missing-report":
            report.unlink()
        elif defect == "invalid-json":
            report.write_bytes(b"invalid JSON")
    destination = tmp_path / "release"
    with pytest.raises(ValueError):
        release.stage(incoming, "1.2.3", destination)
    assert not destination.exists()


@pytest.mark.parametrize(
    "check",
    [
        "in_use_upgrade_rejected",
        "in_use_uninstall_rejected",
        "upgrade_preserved_startup_and_previous_payload",
        "startup_disabled",
        "installed_desktop",
        "uninstall_preserved_preferences_and_unknown_files",
    ],
)
def test_each_installer_acceptance_phase_must_have_passed(incoming, tmp_path, check):
    report = incoming / "artifacts/windows/installer/result.json"
    data = json.loads(report.read_text())
    data[check] = False
    report.write_text(json.dumps(data), encoding="utf-8")
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="acceptance"):
        release.stage(incoming, "1.2.3", destination)
    assert not destination.exists()


def test_invalid_release_version_does_not_create_destination(incoming, tmp_path):
    destination = tmp_path / "release"
    with pytest.raises(ValueError, match="version"):
        release.stage(incoming, "../1.2.3", destination)
    assert not destination.exists()
