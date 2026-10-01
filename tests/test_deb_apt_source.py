"""Verify Debian/Ubuntu base selection against derivative os-release fields."""

from __future__ import annotations

import html
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "packaging/deb/apt-source.sh"


@pytest.mark.parametrize(
    "fields,distro,suite",
    [
        (
            "ID=ubuntu\nVERSION_CODENAME=jammy\nUBUNTU_CODENAME=jammy\n",
            "ubuntu",
            "jammy",
        ),
        ("ID=ubuntu\nVERSION_CODENAME=resolute\n", "ubuntu", "resolute"),
        ("ID=debian\nVERSION_CODENAME=bookworm\n", "debian", "bookworm"),
        ("ID=debian\nVERSION_CODENAME=trixie\n", "debian", "trixie"),
        (
            'ID=linuxmint\nVERSION_CODENAME=zena\nID_LIKE="ubuntu debian"\nUBUNTU_CODENAME=noble\n',
            "ubuntu",
            "noble",
        ),
        (
            "ID=linuxmint\nVERSION_CODENAME=virginia\nUBUNTU_CODENAME=jammy\n",
            "ubuntu",
            "jammy",
        ),
        ("ID=pop\nVERSION_CODENAME=noble\nUBUNTU_CODENAME=noble\n", "ubuntu", "noble"),
    ],
)
def test_selects_distribution_base(tmp_path, fields, distro, suite):
    os_release = tmp_path / "os-release"
    os_release.write_text(fields)
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        env={
            "DOCKING_OS_RELEASE": str(os_release),
            "DOCKING_DEBIAN_VERSION": str(tmp_path / "debian_version"),
        },
        text=True,
        capture_output=True,
        check=True,
    )
    assert f"/deb/{distro}\n" in result.stdout
    assert f"Suites: {suite}\n" in result.stdout
    assert "Signed-By: /etc/apt/keyrings/docking-cloudsmith.asc\n" in result.stdout


@pytest.mark.parametrize(
    "fields",
    [
        "ID=linuxmint\nVERSION_CODENAME=zena\n",
        "ID=ubuntu\nVERSION_CODENAME=focal\n",
        "ID=debian\n",
    ],
)
def test_rejects_unknown_or_unsupported_base(tmp_path, fields):
    os_release = tmp_path / "os-release"
    os_release.write_text(fields)
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        env={
            "DOCKING_OS_RELEASE": str(os_release),
            "DOCKING_DEBIAN_VERSION": str(tmp_path / "debian_version"),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert not result.stdout
    assert "Unsupported APT base" in result.stderr


@pytest.mark.parametrize(
    "version,suite",
    [
        ("12", "bookworm"),
        ("12.11", "bookworm"),
        ("bookworm/sid", "bookworm"),
        ("13.1", "trixie"),
        ("trixie/sid", "trixie"),
        ("sid", None),
        ("14.0", None),
        ("", None),
    ],
)
def test_debian_without_codename(tmp_path, version, suite):
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=debian\nPRETTY_NAME="Debian GNU/Linux bookworm/sid"\n')
    version_file = tmp_path / "debian_version"
    version_file.write_text(version + "\n")
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        env={
            "DOCKING_OS_RELEASE": str(os_release),
            "DOCKING_DEBIAN_VERSION": str(version_file),
        },
        text=True,
        capture_output=True,
    )
    if suite:
        assert result.returncode == 0
        assert "/deb/debian\n" in result.stdout
        assert f"Suites: {suite}\n" in result.stdout
    else:
        assert result.returncode != 0
        assert not result.stdout


@pytest.mark.parametrize("document", ["README.md", "website/index.html"])
@pytest.mark.parametrize("version,supported", [("bookworm/sid", True), ("sid", False)])
def test_documented_setup_validates_before_writing(
    tmp_path, document, version, supported
):
    text = (SCRIPT.parents[2] / document).read_text()
    text = html.unescape(text)
    start = text.index("(\nset -eu\nsudo install -d")
    end = text.index("sudo apt install docking\n)", start)
    command = text[start : end + len("sudo apt install docking\n)")]
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=debian\n")
    version_file = tmp_path / "debian_version"
    version_file.write_text(version + "\n")
    source = tmp_path / "docking.sources"
    source.write_text("existing source\n")
    sudo = tmp_path / "sudo"
    sudo.write_text('#!/bin/sh\nif [ "$1" = tee ]; then shift; cat > "$1"; fi\n')
    sudo.chmod(0o755)
    command = command.replace("/etc/os-release", str(os_release))
    command = command.replace("/etc/debian_version", str(version_file))
    command = command.replace("/etc/apt/sources.list.d/docking.sources", str(source))
    result = subprocess.run(
        ["sh", "-c", command],
        env={"PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
    )
    if supported:
        assert result.returncode == 0, result.stderr
        assert "Suites: bookworm\n" in source.read_text()
        assert "/deb/debian\n" in source.read_text()
    else:
        assert result.returncode != 0
        assert source.read_text() == "existing source\n"
