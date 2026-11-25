from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    shutil.which("dpkg-buildpackage") is None, reason="requires Debian packaging tools"
)
def test_ppa_source_contains_tagged_code_and_current_packaging(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    metadata = checkout / "packaging/deb/debian"
    metadata.mkdir(parents=True)
    build_script = checkout / "packaging/ppa/build.sh"
    build_script.parent.mkdir()
    shutil.copy2(ROOT / "packaging/ppa/build.sh", build_script)
    shutil.copy2(ROOT / "packaging/deb/debian/control", metadata)
    release_file = checkout / "release.txt"
    release_file.write_text("tagged release\n")
    (checkout / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    changelog = metadata / "changelog"
    changelog.write_text(
        "docking (1.2.3-1) stable; urgency=medium\n\n"
        "  * Release 1.2.3.\n\n"
        " -- Test <test@example.com>  Mon, 01 Jun 2026 00:00:00 +0000\n"
    )
    rules = metadata / "rules"
    rules.write_text("#!/usr/bin/make -f\nclean:\n\t:\n")
    rules.chmod(0o755)

    def run(*args):
        subprocess.run(args, cwd=checkout, check=True, capture_output=True)

    run("git", "init", "-q")
    run("git", "add", ".")
    run(
        "git",
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--no-gpg-sign",
        "-qm",
        "Release",
    )
    run("git", "tag", "v1.2.3")
    release_file.write_text("unreleased changes\n")
    rules.write_text(rules.read_text() + "# Updated packaging\n")
    original_changelog = changelog.read_text()
    monkeypatch.setenv("DEBFULLNAME", "Docking Uploader")
    monkeypatch.setenv("DEBEMAIL", "upload@example.com")

    run(str(build_script), "--unsigned", "resolute")

    artifacts = checkout / "artifacts/ppa/unsigned"
    package = "docking_1.2.3-1~ppa1~resolute1"
    changes = (artifacts / f"{package}_source.changes").read_text()
    assert "Distribution: resolute" in changes
    assert "Changed-By: Docking Uploader <upload@example.com>" in changes
    assert "docking_1.2.3.orig.tar.gz" in changes
    unpacked = tmp_path / "unpacked"
    run("dpkg-source", "-x", str(artifacts / f"{package}.dsc"), str(unpacked))
    assert (unpacked / "release.txt").read_text() == "tagged release\n"
    assert (unpacked / "debian/rules").read_text() == rules.read_text()
    assert changelog.read_text() == original_changelog
    assert not (checkout / "debian").exists()
