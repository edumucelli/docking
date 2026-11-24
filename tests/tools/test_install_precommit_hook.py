import os
import subprocess
from pathlib import Path


def test_installer_preserves_globally_shared_hook(tmp_path):
    repo = tmp_path / "repo"
    shared_hooks = tmp_path / "shared-hooks"
    shared_hooks.mkdir()
    shared_hook = shared_hooks / "pre-commit"
    original = b"#!/usr/bin/env bash\nexit 0\n"
    shared_hook.write_bytes(original)
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(["git", "init", "-q", str(repo)], env=env, check=True)
    subprocess.run(
        ["git", "config", "--global", "core.hooksPath", str(shared_hooks)],
        env=env,
        check=True,
    )
    installer = repo / "tools" / "install_precommit_hook.sh"
    installer.parent.mkdir()
    source = Path(__file__).resolve().parents[2] / "tools" / installer.name
    installer.write_bytes(source.read_bytes())

    result = subprocess.run(
        ["bash", str(installer)], env=env, capture_output=True, check=False
    )

    assert shared_hook.read_bytes() == original
    assert result.returncode != 0
