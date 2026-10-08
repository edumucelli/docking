"""Exercise package-install probes and disposable-container retry cleanup."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTAINER_RUNNER = ROOT / "tools/docker-run-timeout.sh"


@pytest.mark.parametrize("supports_timeout_flag", [False, True])
def test_arch_installer_probes_sync_help(tmp_path, supports_timeout_flag):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "pacman.log"
    pacman = bin_dir / "pacman"
    pacman.write_text(
        "#!/bin/bash\n"
        'if [ "$*" = "-S --help" ]; then\n'
        '  [ "$SUPPORTS_TIMEOUT_FLAG" = 0 ] || echo --disable-download-timeout\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$*" = "--help" ]; then echo "general help"; exit 0; fi\n'
        'printf "%s\\n" "$*" >> "$PACMAN_LOG"\n'
    )
    pacman.chmod(0o755)
    # The installer clears a system lock; intercept that operation in this test.
    rm = bin_dir / "rm"
    rm.write_text('#!/bin/bash\n[ "$*" = "-f /var/lib/pacman/db.lck" ]\n')
    rm.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "packaging/arch/install-build-deps.sh"), "--do-install"],
        env=os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PACMAN_LOG": str(log),
            "SUPPORTS_TIMEOUT_FLAG": str(int(supports_timeout_flag)),
        },
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    transactions = log.read_text().splitlines()
    assert len(transactions) == 3
    assert all(
        ("--disable-download-timeout" in transaction) is supports_timeout_flag
        for transaction in transactions
    )


@pytest.fixture
def docker_stub(tmp_path):
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import os, signal, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(os.environ['DOCKER_STUB_ROOT'])\n"
        "mode = os.environ.get('DOCKER_STUB_MODE', 'success')\n"
        "args = sys.argv[1:]\n"
        "def log(message):\n"
        "    with (root / 'docker.log').open('a') as stream:\n"
        "        stream.write(message + '\\n')\n"
        "if args[0] == 'run':\n"
        "    name = args[args.index('--name') + 1]\n"
        "    previous = list(root.glob('container-*'))\n"
        "    if previous:\n"
        "        sys.exit(58)\n"
        "    counter = root / 'attempts'\n"
        "    attempt = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "    counter.write_text(str(attempt))\n"
        "    log('run ' + name)\n"
        "    if mode == 'start_failure':\n"
        "        sys.exit(75)\n"
        "    (root / ('container-' + name)).touch()\n"
        "    if mode == 'hang_first' and attempt == 1:\n"
        "        signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "        while True:\n"
        "            time.sleep(1)\n"
        "    sys.exit(int(os.environ.get('DOCKER_STUB_EXIT', '0')))\n"
        "if args[0] == 'rm':\n"
        "    name = args[-1]\n"
        "    log('rm ' + name)\n"
        "    container = root / ('container-' + name)\n"
        "    if mode == 'cleanup_failure' or not container.exists():\n"
        "        sys.exit(1)\n"
        "    container.unlink()\n"
        "elif args[0] == 'ps':\n"
        "    for container in root.glob('container-*'):\n"
        "        print(container.name)\n"
    )
    docker.chmod(0o755)
    return os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DOCKER_STUB_ROOT": str(tmp_path),
    }


def test_timeout_removes_container_before_retry(tmp_path, docker_stub):
    result = subprocess.run(
        [
            "bash",
            "-c",
            '. "$1"; retry -a 2 -d 0 -j 0 -- bash "$2" 0.5 -- test-image',
            "_",
            str(ROOT / "tools/retry.sh"),
            str(CONTAINER_RUNNER),
        ],
        env=docker_stub | {"DOCKER_STUB_MODE": "hang_first"},
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    events = (tmp_path / "docker.log").read_text().splitlines()
    first, second = events[0].split()[1], events[2].split()[1]
    assert first != second
    assert events == [f"run {first}", f"rm {first}", f"run {second}", f"rm {second}"]
    assert not list(tmp_path.glob("container-*"))
    assert "passed on attempt 2/2" in result.stdout


@pytest.mark.parametrize("exit_status", [0, 42])
def test_container_cleanup_preserves_exit_status(tmp_path, docker_stub, exit_status):
    result = subprocess.run(
        ["bash", str(CONTAINER_RUNNER), "5", "--", "test-image"],
        env=docker_stub | {"DOCKER_STUB_EXIT": str(exit_status)},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == exit_status, result.stderr
    assert not list(tmp_path.glob("container-*"))


def test_failed_creation_remains_retryable(docker_stub):
    result = subprocess.run(
        ["bash", str(CONTAINER_RUNNER), "5", "--", "test-image"],
        env=docker_stub | {"DOCKER_STUB_MODE": "start_failure"},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 75, result.stderr


def test_failed_cleanup_prevents_another_writer(tmp_path, docker_stub):
    result = subprocess.run(
        [
            "bash",
            "-c",
            '. "$1"; retry -a 2 -d 0 -j 0 -- bash "$2" 5 -- test-image',
            "_",
            str(ROOT / "tools/retry.sh"),
            str(CONTAINER_RUNNER),
        ],
        env=docker_stub | {"DOCKER_STUB_MODE": "cleanup_failure"},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 126
    assert (tmp_path / "attempts").read_text() == "1"
    assert "Cannot confirm removal" in result.stderr
