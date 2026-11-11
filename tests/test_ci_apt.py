"""Hosted Ubuntu source formats retain their repositories and signing policy."""

import pytest

from tools.linux.apt_update import normalize_mirrors


@pytest.mark.parametrize(
    "name",
    [
        "sources.list",
        "sources.list.d/ubuntu.list",
        "sources.list.d/ubuntu.sources",
        "apt-mirrors.txt",
        "apt-mirrors-security.txt",
    ],
)
def test_azure_mirror_is_replaced_without_changing_other_source_settings(
    tmp_path, name
):
    source = tmp_path / name
    source.parent.mkdir(parents=True, exist_ok=True)
    original = (
        "URIs: http://azure.archive.ubuntu.com/ubuntu/\n"
        "Suites: noble noble-updates noble-security\nComponents: main universe\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        "https://security.ubuntu.com/ubuntu/\n"
    )
    source.write_text(original)
    normalize_mirrors(tmp_path)
    assert source.read_text() == original.replace(
        "azure.archive.ubuntu.com", "archive.ubuntu.com"
    )
    changed = source.stat().st_mtime_ns
    normalize_mirrors(tmp_path)
    assert source.stat().st_mtime_ns == changed


def test_other_repositories_and_keyrings_are_untouched(tmp_path):
    source = tmp_path / "sources.list"
    source.write_text("deb https://deb.debian.org/debian stable main\n")
    keyring = tmp_path / "trusted.gpg"
    keyring.write_bytes(b"\x00\xff\x80")
    normalize_mirrors(tmp_path)
    assert source.read_text() == "deb https://deb.debian.org/debian stable main\n"
    assert keyring.read_bytes() == b"\x00\xff\x80"
