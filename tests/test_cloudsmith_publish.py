"""Exercise real Debian and RPM metadata plus safe publication/retry decisions."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
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
    monkeypatch.setattr(publisher, "remote_packages", lambda *_: next(responses))
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
        lambda *_: [remote(packages[1], checksum_sha256="different")],
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
    monkeypatch.setattr(publisher, "remote_packages", lambda *_: next(responses))
    sleeps = []
    monkeypatch.setattr(publisher.time, "sleep", sleeps.append)
    publisher.publish("workspace/docking-stable", packages)
    assert sleeps == [10]


def test_verification_times_out_on_unindexed_packages(monkeypatch, packages):
    monkeypatch.setattr(
        publisher,
        "remote_packages",
        lambda *_: [remote(package, indexed=False) for package in packages],
    )
    with pytest.raises(TimeoutError):
        publisher.publish("workspace/docking-stable", packages, timeout=0)


RPM_PAIR = [
    ("docking-2.13.6-1.x86_64.rpm", ("docking", "2.13.6", "x86_64", "1")),
    ("docking-2.13.6-1.aarch64.rpm", ("docking", "2.13.6", "aarch64", "1")),
]


def build_rpm_set(tmp_path, monkeypatch, entries):
    """Create RPM fixtures whose metadata comes from the given mapping.

    rpmbuild can only produce the host architecture, so the fixtures stand in
    for the real header and the query is replaced with the recorded metadata.
    """
    incoming = tmp_path / "incoming"
    incoming.mkdir(exist_ok=True)
    metadata = {}
    for filename, fields in entries:
        (incoming / filename).write_bytes(filename.encode())
        metadata[filename] = fields
    monkeypatch.setitem(
        publisher.FORMATS,
        "rpm",
        dataclasses.replace(
            publisher.FORMATS["rpm"], fields=lambda path: metadata[path.name]
        ),
    )
    return incoming


def release_rpm(tmp_path, monkeypatch, entries, version="2.13.6"):
    incoming = build_rpm_set(tmp_path, monkeypatch, entries)
    return publisher.release_packages(incoming, version, publisher.FORMATS["rpm"])


@pytest.fixture
def rpm_packages(tmp_path, monkeypatch):
    return release_rpm(tmp_path, monkeypatch, RPM_PAIR)


def rpm_remote(package, **changes):
    return {
        "name": "docking",
        "format": "rpm",
        "version": package.version,
        "release": package.release,
        "architectures": [{"name": package.architecture}],
        # Cloudsmith signs RPMs on upload, so published bytes never match the
        # artifact the release shipped.
        "checksum_sha256": "0" * 64,
        "distro": {"slug": "any-distro"},
        "distro_version": {"slug": "any-version"},
        "is_sync_completed": True,
        "is_downloadable": True,
        "indexed": True,
        **changes,
    }


def test_deb_remains_the_default_format(packages):
    assert [package.architecture for package in packages] == ["amd64", "arm64"]
    assert packages[0].release == ""


def test_rpm_uses_native_architectures_and_release(rpm_packages):
    assert sorted(package.architecture for package in rpm_packages) == [
        "aarch64",
        "x86_64",
    ]
    assert {(package.version, package.release) for package in rpm_packages} == {
        ("2.13.6", "1")
    }


def test_rpm_missing_architecture_fails_before_publication(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="exactly one"):
        release_rpm(tmp_path, monkeypatch, RPM_PAIR[:1])


def test_rpm_duplicate_architecture_is_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="exactly one"):
        release_rpm(
            tmp_path,
            monkeypatch,
            [
                ("docking-2.13.6-1.x86_64.rpm", ("docking", "2.13.6", "x86_64", "1")),
                (
                    "docking-2.13.6-1-copy.x86_64.rpm",
                    ("docking", "2.13.6", "x86_64", "1"),
                ),
                ("docking-2.13.6-1.aarch64.rpm", ("docking", "2.13.6", "aarch64", "1")),
            ],
        )


@pytest.mark.parametrize(
    "fields",
    [
        ("docking-dbgsym", "2.13.6", "x86_64", "1"),
        ("docking", "2.13.5", "x86_64", "1"),
        ("docking", "2.13.6", "noarch", "1"),
        ("docking", "2.13.6", "x86_64", "beta"),
    ],
)
def test_rejects_unexpected_rpm_packages(tmp_path, monkeypatch, fields):
    name, version, architecture, release = fields
    filename = f"{name}-{version}-{release}.{architecture}.rpm"
    with pytest.raises(ValueError):
        release_rpm(tmp_path, monkeypatch, [(filename, fields)])


def test_rpm_rejects_different_releases(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="same RPM version and release"):
        release_rpm(
            tmp_path,
            monkeypatch,
            [
                ("docking-2.13.6-1.x86_64.rpm", ("docking", "2.13.6", "x86_64", "1")),
                ("docking-2.13.6-2.aarch64.rpm", ("docking", "2.13.6", "aarch64", "2")),
            ],
        )


def test_rpm_already_published_is_not_reuploaded(monkeypatch, rpm_packages):
    """Cloudsmith re-signs on upload, so matching bytes cannot be asserted."""
    monkeypatch.setattr(
        publisher,
        "remote_packages",
        lambda *_: [rpm_remote(package) for package in rpm_packages],
    )
    uploads = []
    monkeypatch.setattr(
        publisher.subprocess, "run", lambda command, **_: uploads.append(command)
    )
    publisher.publish(
        "workspace/docking-rpm", rpm_packages, fmt=publisher.FORMATS["rpm"]
    )
    assert uploads == []


def test_rpm_upload_targets_the_repository_without_a_component(
    monkeypatch, rpm_packages
):
    complete = [rpm_remote(package) for package in rpm_packages]
    responses = iter([[], complete])
    monkeypatch.setattr(
        publisher, "remote_packages", lambda *_: next(responses, complete)
    )
    uploads = []
    monkeypatch.setattr(
        publisher.subprocess, "run", lambda command, **_: uploads.append(command)
    )
    publisher.publish(
        "workspace/docking-rpm", rpm_packages, fmt=publisher.FORMATS["rpm"]
    )
    assert len(uploads) == 2
    assert uploads[0][:4] == [
        "cloudsmith",
        "push",
        "rpm",
        "workspace/docking-rpm/any-distro/any-version",
    ]
    assert "--component" not in uploads[0]
    assert str(rpm_packages[1].path) in uploads[1]


def test_rpm_accepts_a_combined_remote_version(monkeypatch, rpm_packages):
    """Cloudsmith may report the RPM version with or without the release."""
    combined = [
        rpm_remote(package, version=f"{package.version}-{package.release}")
        for package in rpm_packages
    ]
    monkeypatch.setattr(publisher, "remote_packages", lambda *_: combined)
    uploads = []
    monkeypatch.setattr(
        publisher.subprocess, "run", lambda command, **_: uploads.append(command)
    )
    publisher.publish(
        "workspace/docking-rpm", rpm_packages, fmt=publisher.FORMATS["rpm"]
    )
    assert uploads == []


def test_rpm_release_must_match_when_cloudsmith_reports_one(rpm_packages):
    """A bare version alone must not be mistaken for a different Release."""
    assert (
        publisher.existing_package(
            rpm_packages[0],
            [rpm_remote(rpm_packages[0], release="2")],
            publisher.FORMATS["rpm"],
        )
        is None
    )


def test_rpm_matches_a_bare_version_when_the_release_agrees(rpm_packages):
    match = publisher.existing_package(
        rpm_packages[0],
        [rpm_remote(rpm_packages[0])],
        publisher.FORMATS["rpm"],
    )
    assert match is not None


def test_rpm_list_query_requests_the_rpm_format(monkeypatch):
    recorded = {}

    def fake_check_output(command, **_):
        recorded["command"] = command
        return json.dumps({"data": []})

    monkeypatch.setattr(publisher.subprocess, "check_output", fake_check_output)
    publisher.remote_packages("workspace/docking-rpm", publisher.FORMATS["rpm"])
    assert "format:rpm name:^docking$" in recorded["command"]


@pytest.mark.parametrize(
    "changes",
    [
        {"is_quarantined": True},
        {"is_sync_failed": True},
        {"is_hidden": True},
        {"distro": {"slug": "fedora"}},
    ],
)
def test_rejects_unusable_remote_rpm_packages(rpm_packages, changes):
    with pytest.raises(ValueError):
        publisher.existing_package(
            rpm_packages[0],
            [rpm_remote(rpm_packages[0], **changes)],
            publisher.FORMATS["rpm"],
        )
