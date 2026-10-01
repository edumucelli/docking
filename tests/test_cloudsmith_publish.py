"""Exercise real Debian metadata and safe publication/retry decisions."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "packaging/cloudsmith/publish.py"
spec = importlib.util.spec_from_file_location("docking_cloudsmith_publish", MODULE_PATH)
assert spec and spec.loader
publisher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = publisher
spec.loader.exec_module(publisher)


def build_deb(tmp_path, architecture, version="2.13.6-1", name="docking", suffix=""):
    root = tmp_path / f"root-{architecture}{suffix}"
    control = root / "DEBIAN/control"
    control.parent.mkdir(parents=True)
    control.write_text(
        f"Package: {name}\nVersion: {version}\nArchitecture: {architecture}\n"
        "Maintainer: Test <test@example.com>\nDescription: Publisher fixture\n"
    )
    incoming = tmp_path / "incoming"
    incoming.mkdir(exist_ok=True)
    output = incoming / f"{name}_{version}_{architecture}{suffix}.deb"
    subprocess.run(
        ["dpkg-deb", "--build", str(root), str(output)], check=True, capture_output=True
    )
    return output


@pytest.fixture
def packages(tmp_path):
    build_deb(tmp_path, "amd64")
    build_deb(tmp_path, "arm64")
    return publisher.release_packages(tmp_path / "incoming", "2.13.6")


def remote(package, **changes):
    return {
        "name": "docking",
        "format": "deb",
        "version": package.version,
        "architectures": [{"name": package.architecture}],
        "checksum_sha256": package.sha256,
        "distro": {"slug": "any-distro"},
        "distro_version": {"slug": "any-version"},
        "is_sync_completed": True,
        "is_downloadable": True,
        "indexed": True,
        **changes,
    }


def test_validates_metadata_independently_of_asset_names(packages):
    assert [package.architecture for package in packages] == ["amd64", "arm64"]
    assert all(len(package.sha256) == 64 for package in packages)


def test_missing_architecture_fails_before_publication(tmp_path):
    build_deb(tmp_path, "amd64")
    with pytest.raises(ValueError, match="exactly one"):
        publisher.release_packages(tmp_path / "incoming", "2.13.6")


def test_duplicate_architecture_is_rejected(tmp_path):
    build_deb(tmp_path, "amd64")
    build_deb(tmp_path, "amd64", suffix="-duplicate")
    build_deb(tmp_path, "arm64")
    with pytest.raises(ValueError, match="exactly one"):
        publisher.release_packages(tmp_path / "incoming", "2.13.6")


@pytest.mark.parametrize(
    "name,version,architecture",
    [
        ("docking-dbgsym", "2.13.6-1", "amd64"),
        ("docking", "2.13.5-1", "amd64"),
        ("docking", "2.13.6-1", "all"),
    ],
)
def test_rejects_unexpected_release_packages(tmp_path, name, version, architecture):
    build_deb(tmp_path, architecture, version, name)
    with pytest.raises(ValueError):
        publisher.release_packages(tmp_path / "incoming", "2.13.6")


def test_rejects_different_debian_revisions(tmp_path):
    build_deb(tmp_path, "amd64")
    build_deb(tmp_path, "arm64", version="2.13.6-2")
    with pytest.raises(ValueError, match="same Debian package version"):
        publisher.release_packages(tmp_path / "incoming", "2.13.6")


def test_partial_retry_uploads_only_missing_architecture(monkeypatch, packages):
    responses = iter([[remote(packages[0])], [remote(package) for package in packages]])
    monkeypatch.setattr(publisher, "remote_packages", lambda _: next(responses))
    uploads = []
    monkeypatch.setattr(
        publisher.subprocess, "run", lambda command, **_: uploads.append(command)
    )
    publisher.publish("workspace/docking-stable", packages)
    assert len(uploads) == 1
    assert str(packages[1].path) in uploads[0]
    assert "--republish" not in uploads[0]


def test_conflicting_second_architecture_prevents_first_upload(monkeypatch, packages):
    monkeypatch.setattr(
        publisher,
        "remote_packages",
        lambda _: [remote(packages[1], checksum_sha256="different")],
    )
    uploads = []
    monkeypatch.setattr(
        publisher.subprocess, "run", lambda *_, **__: uploads.append(True)
    )
    with pytest.raises(ValueError, match="Conflicting published bytes"):
        publisher.publish("workspace/docking-stable", packages)
    assert not uploads


@pytest.mark.parametrize(
    "changes",
    [
        {"is_quarantined": True},
        {"is_sync_failed": True},
        {"is_hidden": True},
        {"distro": {"slug": "ubuntu"}},
    ],
)
def test_rejects_unusable_remote_packages(packages, changes):
    with pytest.raises(ValueError):
        publisher.existing_package(packages[0], [remote(packages[0], **changes)])


def test_waits_for_existing_package_to_be_indexed(monkeypatch, packages):
    complete = [remote(package) for package in packages]
    responses = iter(
        [complete, [remote(package, indexed=False) for package in packages], complete]
    )
    monkeypatch.setattr(publisher, "remote_packages", lambda _: next(responses))
    sleeps = []
    monkeypatch.setattr(publisher.time, "sleep", sleeps.append)
    publisher.publish("workspace/docking-stable", packages)
    assert sleeps == [10]


def test_verification_times_out_on_unindexed_packages(monkeypatch, packages):
    monkeypatch.setattr(
        publisher,
        "remote_packages",
        lambda _: [remote(package, indexed=False) for package in packages],
    )
    with pytest.raises(TimeoutError):
        publisher.publish("workspace/docking-stable", packages, timeout=0)
