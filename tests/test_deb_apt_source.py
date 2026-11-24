"""Verify platform selection and signed repository setup for the Linux installer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging/install.sh"


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
        ["sh", str(SCRIPT), "--print-source"],
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
        ["sh", str(SCRIPT), "--print-source"],
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
        ["sh", str(SCRIPT), "--print-source"],
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


@pytest.fixture
def apt_setup(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    apt_dir = tmp_path / "apt"
    (apt_dir / "sources.list.d").mkdir(parents=True)
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=ubuntu\nVERSION_CODENAME=jammy\n")
    certificates = tmp_path / "ca-certificates.crt"
    certificates.write_text("fixture certificates")
    log = tmp_path / "sudo.log"
    log.touch()
    program = (ROOT / "docs/INSTALLATION.md").read_text()
    snippets = (block.split("\n```", 1)[0] for block in program.split("```sh\n")[1:])
    snippet = next(
        block for block in snippets if "https://docking.cc/install.sh" in block
    )
    installer = tmp_path / "install.sh"
    script = SCRIPT.read_text()
    script = script.replace("/etc/apt", str(apt_dir))
    script = script.replace("/etc/os-release", str(os_release))
    script = script.replace("/etc/ssl/certs/ca-certificates.crt", str(certificates))
    script = script.replace("/etc/ssl/ca-bundle.pem", str(certificates))
    for path in ("/etc/yum.repos.d", "/etc/zypp/repos.d", "/etc/pki/rpm-gpg"):
        script = script.replace(path, str(tmp_path / path.removeprefix("/etc/")))
    installer.write_text(script)

    for name in (
        "awk",
        "cat",
        "cp",
        "env",
        "grep",
        "install",
        "ln",
        "mktemp",
        "rm",
        "sha256sum",
        "sh",
    ):
        executable = shutil.which(name)
        if executable is None:
            pytest.skip(f"{name} is not installed")
        (commands / name).symlink_to(executable)

    for name, body in {
        "dpkg": 'printf "%s\n" "${FIXTURE_ARCH:-amd64}"',
        "sudo": """printf '%s\\n' "$*" >> "$FIXTURE_LOG"
if [ "$1" = install ] && [ "$2" = -m ] && [ "${5##*/}" = "${FIXTURE_INTERRUPT_FILE:-}" ]; then
    printf 'partial write' > "$6"
    exit 1
fi
if [ "$1" = ln ] && [ "${4##*/}" = docking.sources ]; then
    case "${FIXTURE_CONCURRENT_SOURCE:-}" in
        matching) cp "$3" "$4" ;;
        custom) printf 'Enabled: no\\n' > "$4" ;;
    esac
fi
exec "$@"
""",
        "curl": """if [ "$2" = https://docking.cc/install.sh ]; then
    cat "$FIXTURE_INSTALLER"
    exit "${FIXTURE_DOWNLOAD_STATUS:-0}"
fi
[ "${FIXTURE_KEY:-valid}" != missing ] && printf 'fixture key\\n' > "$4"
""",
        "gpg": """case "${FIXTURE_KEY:-valid}" in
    invalid) printf 'pub:\nfpr:::::::::BADKEY:\n'; exit ;;
    malformed) exit 2 ;;
esac
printf 'pub:\nfpr:::::::::%s:\n' "${FIXTURE_FINGERPRINT:-811B48CD4A69170DD98F4F49185CED80A7947754}"
printf 'sub:\nfpr:::::::::SUBKEY:\n'
if [ "${FIXTURE_KEY:-valid}" = extra ]; then
    printf 'pub:\nfpr:::::::::EXTRAKEY:\n'
fi""",
        "apt-get": """if [ "$1" = update ]; then
    case "$*" in
        'update -o APT::Update::Error-Mode=any')
            printf 'distro indexes\\n' > "$FIXTURE_LISTS"
            exit 100 ;;
        *Dir::Etc::sourceparts=-*APT::Get::List-Cleanup=0*APT::Update::Error-Mode=any*)
            exit "${FIXTURE_REFRESH_STATUS:-0}" ;;
        *) exit 100 ;;
    esac
fi
if [ "$1" = install ]; then
    shift
    for package do
        case "$package" in
            curl) cp "$FIXTURE_CURL" "$FIXTURE_BIN/curl" ;;
            ca-certificates) printf 'fixture certificates\\n' > "$FIXTURE_CERTIFICATES" ;;
            docking:*)
                if [ "${FIXTURE_EMPTY_LISTS:-0}" = 1 ]; then test -s "$FIXTURE_LISTS" || exit 100; fi ;;
        esac
    done
fi""",
        "apt-cache": 'printf "%s\n" "$FIXTURE_POLICY"',
        "apt-mark": 'printf "%s" "${FIXTURE_HOLD:-}"',
        "uname": 'printf "x86_64\n"',
        "rpm": '[ "$1" = --import ]',
        "dnf": "exit 0",
        "zypper": "exit 0",
    }.items():
        stub = commands / name
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)

    curl_package = tmp_path / "curl"
    (commands / "curl").rename(curl_package)
    (commands / "curl").symlink_to(curl_package)

    def run(
        shell="sh", *, curl_available=True, downloaded=False, release=None, **options
    ):
        executable = shutil.which(shell)
        if executable is None:
            pytest.skip(f"{shell} is not installed")
        if not curl_available:
            (commands / "curl").unlink()
        if release is not None:
            os_release.write_text(release)
        args = (
            [executable, str(installer)] if downloaded else [executable, "-c", snippet]
        )
        return subprocess.run(
            args,
            env={
                "PATH": str(commands),
                "TMPDIR": str(tmp_path),
                "FIXTURE_INSTALLER": str(installer),
                "FIXTURE_BIN": str(commands),
                "FIXTURE_CURL": str(curl_package),
                "FIXTURE_CERTIFICATES": str(certificates),
                "FIXTURE_LISTS": str(tmp_path / "distro-indexes"),
                "FIXTURE_LOG": str(log),
                "FIXTURE_POLICY": (
                    "docking:\n  Installed: (none)\n  Candidate: 2.34.0-1\n"
                    "  Version table:\n     2.34.0-1 500\n"
                    "        500 https://dl.cloudsmith.io/public/docking/"
                    "docking-apt/deb/ubuntu jammy/main amd64 Packages"
                ),
                **options,
            },
            text=True,
            capture_output=True,
        )

    return run, apt_dir, log, certificates


@pytest.mark.parametrize(
    "release,repo_dir,transaction",
    [
        (
            "ID=fedora\nVERSION_ID=44\n",
            "yum.repos.d",
            "dnf --refresh install --from-repo=docking-rpm docking",
        ),
        (
            "ID=opensuse-leap\nVERSION_ID=16.0\n",
            "zypp/repos.d",
            "zypper install --from docking-rpm docking",
        ),
        (
            "ID=opensuse-tumbleweed\nVERSION_ID=20261124\n",
            "zypp/repos.d",
            "zypper install --from docking-rpm docking",
        ),
    ],
)
def test_installer_adds_the_signed_repository_and_installs_rpm(
    apt_setup, release, repo_dir, transaction
):
    run, apt_dir, log, _ = apt_setup
    result = run(
        downloaded=True,
        release=release,
        FIXTURE_FINGERPRINT="04C7240DAD480161C0DFC791859E104126138494",
    )
    assert result.returncode == 0, result.stderr
    repo = (apt_dir.parent / repo_dir / "docking-rpm.repo").read_text()
    settings = repo.splitlines()
    assert settings.count("repo_gpgcheck=1") == 2
    assert settings.count("gpgcheck=1") == 2
    assert "gpgkey=file://" in repo
    commands = log.read_text()
    if repo_dir == "zypp/repos.d":
        assert settings.count("autorefresh=1") == 2
        assert "zypper refresh docking-rpm docking-rpm-noarch" in commands
    assert transaction in commands
    assert not (apt_dir / "sources.list.d/docking.sources").exists()

    log.write_text("")
    retry = run(
        downloaded=True, FIXTURE_FINGERPRINT="04C7240DAD480161C0DFC791859E104126138494"
    )
    assert retry.returncode == 0, retry.stderr
    assert "install -m" not in log.read_text()
    assert transaction in log.read_text()


def test_rpm_setup_rejects_an_unverified_key(apt_setup):
    run, apt_dir, log, _ = apt_setup
    result = run(downloaded=True, release="ID=fedora\nVERSION_ID=44\n")
    assert result.returncode != 0
    assert "Docking signing key verification failed" in result.stderr
    assert not log.read_text()
    assert not (apt_dir.parent / "yum.repos.d" / "docking-rpm.repo").exists()


@pytest.mark.parametrize("shell", ["sh", "bash", "zsh"])
def test_documented_setup_installs_and_reuses_matching_configuration(shell, apt_setup):
    run, apt_dir, log, _ = apt_setup
    first = run(shell)
    assert first.returncode == 0, first.stderr
    source = apt_dir / "sources.list.d/docking.sources"
    assert "Suites: jammy\n" in source.read_text()
    keyring = apt_dir / "keyrings/docking-cloudsmith.asc"
    assert keyring.read_text() == "fixture key\n"
    log.write_text("")
    second = run(shell)
    assert second.returncode == 0, second.stderr
    assert "install -m" not in log.read_text()
    assert "apt-get install docking:amd64" in log.read_text()


def test_documented_setup_rejects_unsupported_arch_before_sudo(apt_setup):
    run, apt_dir, log, _ = apt_setup
    result = run(FIXTURE_ARCH="i386")
    assert result.returncode != 0
    assert "Unsupported architecture: i386" in result.stderr
    assert not log.read_text()
    assert not (apt_dir / "keyrings").exists()


def test_downloaded_installer_installs_missing_prerequisites(apt_setup):
    run, apt_dir, log, certificates = apt_setup
    certificates.unlink()
    result = run(curl_available=False, downloaded=True)
    assert result.returncode == 0, result.stderr
    assert log.read_text().startswith(
        "apt-get update -o APT::Update::Error-Mode=any\n"
        "apt-get install curl ca-certificates\n"
    )
    assert (apt_dir / "sources.list.d/docking.sources").exists()


def test_documented_setup_does_not_run_a_failed_download(apt_setup):
    run, apt_dir, log, _ = apt_setup
    result = run(FIXTURE_DOWNLOAD_STATUS="18")
    assert result.returncode == 18
    assert not log.read_text()
    assert not (apt_dir / "keyrings").exists()


@pytest.mark.parametrize("key", ["missing", "malformed", "invalid", "extra"])
def test_documented_setup_rejects_unverified_keys(apt_setup, key):
    run, apt_dir, log, _ = apt_setup
    result = run(FIXTURE_KEY=key)
    assert result.returncode != 0
    assert not log.read_text()
    assert not (apt_dir / "keyrings").exists()
    assert not (apt_dir / "sources.list.d/docking.sources").exists()


@pytest.mark.parametrize(
    "path,content",
    [
        (
            "sources.list",
            "deb https://dl.cloudsmith.io/public/docking/docking-apt/deb/ubuntu jammy main\n",
        ),
        (
            "sources.list.d/older.list",
            "deb https://dl.cloudsmith.io/public/docking/docking-apt/deb/ubuntu jammy main\n",
        ),
        (
            "sources.list.d/older.sources",
            "URIs:\n https://DL.CLOUDSMITH.IO/public/docking/docking-apt/deb/ubuntu\n",
        ),
        ("sources.list.d/docking.sources", "Enabled: no\n"),
        ("keyrings/docking-cloudsmith.asc", "custom key\n"),
    ],
)
def test_documented_setup_preserves_conflicting_files(apt_setup, path, content):
    run, apt_dir, log, _ = apt_setup
    conflict = apt_dir / path
    conflict.parent.mkdir(exist_ok=True)
    conflict.write_text(content)
    result = run()
    assert result.returncode != 0
    assert str(conflict) in result.stderr
    assert conflict.read_text() == content
    assert not log.read_text()


def test_documented_setup_uses_cached_lists_when_an_unrelated_refresh_fails(apt_setup):
    run, _, log, certificates = apt_setup
    certificates.unlink()
    result = run()
    assert result.returncode == 0, result.stderr
    assert "APT refresh reported errors" in result.stderr
    commands = log.read_text()
    assert commands.startswith(
        "apt-get update -o APT::Update::Error-Mode=any\n"
        "apt-get install ca-certificates\n"
    )
    assert commands.count("apt-get update -o APT::Update::Error-Mode=any\n") == 1
    assert "apt-get install docking:amd64" in commands


def test_documented_setup_refreshes_empty_distro_indexes(apt_setup):
    run, _, log, _ = apt_setup
    result = run(FIXTURE_EMPTY_LISTS="1")
    assert result.returncode == 0, result.stderr
    assert "APT refresh reported errors" in result.stderr
    commands = log.read_text()
    assert "apt-get update -o APT::Update::Error-Mode=any\n" in commands
    assert "apt-get install docking:amd64" in commands


@pytest.mark.parametrize(
    "staged_name,destination",
    [
        ("key.asc", "keyrings/docking-cloudsmith.asc"),
        ("docking.sources", "sources.list.d/docking.sources"),
    ],
)
def test_documented_setup_can_retry_after_a_partial_file_write(
    apt_setup, staged_name, destination
):
    run, apt_dir, _, _ = apt_setup
    final = apt_dir / destination
    failure = run(FIXTURE_INTERRUPT_FILE=staged_name)
    assert failure.returncode != 0, failure.stderr
    assert not final.exists()
    assert not list(final.parent.glob(".docking.*"))
    retry = run()
    assert retry.returncode == 0, retry.stderr
    assert final.exists()


@pytest.mark.parametrize("concurrent_source", ["matching", "custom"])
def test_documented_setup_preserves_a_concurrently_created_source(
    apt_setup, concurrent_source
):
    run, apt_dir, log, _ = apt_setup
    result = run(FIXTURE_CONCURRENT_SOURCE=concurrent_source)
    final = apt_dir / "sources.list.d/docking.sources"
    assert not list(final.parent.glob(".docking.*"))
    if concurrent_source == "matching":
        assert result.returncode == 0, result.stderr
        assert "Suites: jammy\n" in final.read_text()
    else:
        assert result.returncode != 0
        assert str(final) in result.stderr
        assert final.read_text() == "Enabled: no\n"
        assert "apt-get install docking" not in log.read_text()


def test_documented_setup_ignores_docking_urls_in_unrelated_metadata(apt_setup):
    run, apt_dir, _, _ = apt_setup
    (apt_dir / "sources.list.d/unrelated.sources").write_text(
        "Types: deb\nURIs: https://example.test/debian\nSuites: stable\n"
        "Components: main\nX-Notes: https://dl.cloudsmith.io/public/docking/docking-apt/\n"
    )
    result = run()
    assert result.returncode == 0, result.stderr


def test_documented_setup_stops_on_docking_refresh_failure_and_can_retry(apt_setup):
    run, _, log, _ = apt_setup
    failure = run(FIXTURE_REFRESH_STATUS="100")
    assert failure.returncode != 0
    assert "Could not refresh the signed Docking repository" in failure.stderr
    assert "apt-get install docking" not in log.read_text()
    retry = run()
    assert retry.returncode == 0, retry.stderr


def test_documented_setup_requires_a_candidate_from_docking(apt_setup):
    run, _, log, _ = apt_setup
    failure = run(FIXTURE_POLICY="docking:\n  Candidate: 2.10.0-1")
    assert failure.returncode != 0
    assert "APT did not select a Docking version" in failure.stderr
    assert "apt-get install docking" not in log.read_text()


def test_documented_setup_rejects_ambiguous_candidate_origin(apt_setup):
    run, _, log, _ = apt_setup
    result = run(
        FIXTURE_POLICY=(
            "docking:\n  Candidate: 2.34.0-1\n  Version table:\n     2.34.0-1 500\n"
            "        500 file:/srv/apt ./ Packages\n"
            "        500 https://dl.cloudsmith.io/public/docking/"
            "docking-apt/deb/ubuntu jammy/main amd64 Packages\n"
        )
    )
    assert result.returncode != 0
    assert "APT did not select a Docking version" in result.stderr
    assert "apt-get install docking" not in log.read_text()


def test_documented_setup_respects_a_hold(apt_setup):
    run, _, log, _ = apt_setup
    failure = run(FIXTURE_HOLD="docking")
    assert failure.returncode != 0
    assert "Docking is held in APT" in failure.stderr
    assert "apt-get install docking" not in log.read_text()
